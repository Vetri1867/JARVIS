import { Room, RoomEvent, RemoteTrack, Track } from 'livekit-client';

export interface VoiceAssistant {
  connect(url: string, token: string): Promise<void>;
  disconnect(): void;
  setMute(muted: boolean): Promise<void>;
  sendData(data: any): Promise<void>;
  getAnalyser(): AnalyserNode | undefined;
  onTranscript(cb: (text: string) => void): void;
  onStateChange(cb: (state: "idle" | "listening" | "thinking" | "speaking") => void): void;
}

/**
 * Standard LiveKit Voice Assistant Implementation.
 */
export function createLiveKitVoice(): VoiceAssistant {
  let room: Room | null = null;
  let transcriptCallback: ((text: string) => void) | null = null;
  let stateCallback: ((state: "idle" | "listening" | "thinking" | "speaking") => void) | null = null;
  let analyserNode: AnalyserNode | undefined;
  let audioContext: AudioContext | null = null;

  return {
    async connect(url: string, token: string) {
      room = new Room();
      
      room
        .on(RoomEvent.TrackSubscribed, (track: RemoteTrack) => {
          if (track.kind === Track.Kind.Audio) {
            console.log("[livekit] 🔊 SHADOW audio track subscribed");
            
            // Create a standard Web Audio Analyser for the orb
            if (!audioContext) audioContext = new (window.AudioContext || (window as any).webkitAudioContext)();
            const source = audioContext.createMediaStreamSource(new MediaStream([track.mediaStreamTrack]));
            analyserNode = audioContext.createAnalyser();
            analyserNode.fftSize = 256;
            source.connect(analyserNode);
            
            // Play the track
            track.attach();
          }
        })
        .on(RoomEvent.TrackPublished, (publication) => {
          if (publication.track?.kind === Track.Kind.Audio) {
            console.log("[livekit] 🎤 Microphone track published successfully");
          }
        })
        .on(RoomEvent.ParticipantAttributesChanged, (_, participant) => {
          const state = participant.attributes['agent.state'];
          if (state) {
            console.log("[livekit] SHADOW state changed:", state);
            stateCallback?.(state as any);
          }
        })
        .on(RoomEvent.DataReceived, (payload: Uint8Array, participant) => {
          if (participant) {
            try {
              const decoder = new TextDecoder();
              const data = JSON.parse(decoder.decode(payload));
              if (data.type === "transcript") {
                transcriptCallback?.(data.text);
              }
            } catch (e) {
              console.warn("[livekit] error decoding data packet", e);
            }
          }
        })
        .on(RoomEvent.Disconnected, () => {
          console.log("[livekit] ❌ Disconnected from room");
          analyserNode = undefined;
        });

      await room.connect(url, token);
      console.log("[livekit] ✅ Connected to room:", room.name);
      
      // Force microphone publication
      console.log("[livekit] 🎙️ Enabling microphone...");
      await room.localParticipant.setMicrophoneEnabled(true);
      console.log("[livekit] 🎤 MIC WORKING - Audio track published");
    },

    disconnect() {
      room?.disconnect();
      room = null;
      analyserNode = undefined;
    },

    async setMute(muted: boolean) {
      await room?.localParticipant.setMicrophoneEnabled(!muted);
    },

    async sendData(data: any) {
      if (!room) return;
      const encoder = new TextEncoder();
      const payload = encoder.encode(JSON.stringify(data));
      await room.localParticipant.publishData(payload, { reliable: true });
    },

    getAnalyser() {
      return analyserNode;
    },

    onTranscript(cb: (text: string) => void) {
      transcriptCallback = cb;
    },

    onStateChange(cb: (state: "idle" | "listening" | "thinking" | "speaking") => void) {
      stateCallback = cb;
    }
  };
}

/**
 * Local WebSpeech-based assistant (no LiveKit required).
 *
 * - STT: Chrome Web Speech API (SpeechRecognition)
 * - TTS: speechSynthesis
 * - Orb analyser: microphone analyser (getUserMedia)
 * - Chat: POST /api/chat
 */
export function createWebVoice(): VoiceAssistant {
  let transcriptCallback: ((text: string) => void) | null = null;
  let stateCallback: ((state: "idle" | "listening" | "thinking" | "speaking") => void) | null = null;

  let muted = false;
  let recognition: any | null = null;
  let recognitionEnabled = false;
  let recognitionStarting = false;
  let restartDelayMs = 500;
  let fatalSttError: string | null = null;
  let useGeminiStt = false;
  let recorder: MediaRecorder | null = null;
  let recordChunks: BlobPart[] = [];
  let recordingMime = "";
  let geminiLoopActive = false;
  let geminiSpeechStarted = false;
  let geminiSilenceMs = 0;
  let audioContext: AudioContext | null = null;
  let analyserNode: AnalyserNode | undefined;
  let micStream: MediaStream | null = null;

  // Wake word gating ("shadow", "hey shadow")
  let armedUntilMs = 0;
  const WAKE_WORDS = ["shadow", "hey shadow", "hi shadow", "okay shadow", "ok shadow"];
  const ARM_WINDOW_MS = 8000;

  function setState(s: "idle" | "listening" | "thinking" | "speaking") {
    stateCallback?.(s);
  }

  async function ensureMicAnalyser() {
    if (analyserNode) return;
    audioContext = audioContext || new (window.AudioContext || (window as any).webkitAudioContext)();
    // Some browsers create AudioContext suspended until a gesture; attempt resume.
    try {
      if (audioContext.state === "suspended") await audioContext.resume();
    } catch {}
    const md = navigator.mediaDevices;
    if (!md?.getUserMedia) throw new Error("mediaDevices.getUserMedia not available");

    // Log what Chrome can see (helps debug "Requested device not found")
    try {
      const devices = await md.enumerateDevices?.();
      const inputs = (devices || []).filter((d) => d.kind === "audioinput");
      transcriptCallback?.(`[mic] audio inputs detected: ${inputs.length}`);
      if (inputs.length > 0) {
        const names = inputs
          .slice(0, 5)
          .map((d, i) => `${i + 1}) ${d.label || "(label hidden until permission granted)"}`);
        transcriptCallback?.(`[mic] devices: ${names.join(" | ")}`);
      }
    } catch {
      // ignore
    }

    // First try: higher-quality raw mic (may fail on some drivers/devices)
    try {
      micStream = await md.getUserMedia({
        audio: {
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
          channelCount: 1,
        } as any,
      });
    } catch (e: any) {
      const errName = e?.name || e?.error || "getUserMedia failed";
      transcriptCallback?.(`[mic] primary constraints failed: ${errName}`);
      // Fallback: let browser choose any working mic
      micStream = await md.getUserMedia({ audio: true });
    }

    const track = micStream.getAudioTracks?.()?.[0];
    if (!track) throw new Error("No microphone track available");
    if ((track as any).muted) transcriptCallback?.("[mic] microphone track is muted by the OS/app");
    const source = audioContext.createMediaStreamSource(micStream);
    analyserNode = audioContext.createAnalyser();
    analyserNode.fftSize = 256;
    source.connect(analyserNode);
  }

  function ensureRecognition() {
    if (recognition) return;
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) return;

    recognition = new SR();
    recognition.continuous = true;
    recognition.interimResults = false;
    recognition.lang = (navigator.language || "en-US") as any;

    recognition.onstart = () => {
      recognitionStarting = false;
      if (!muted) setState(Date.now() <= armedUntilMs ? "listening" : "idle");
    };

    recognition.onresult = async (event: any) => {
      if (muted) return;
      const last = event.results?.[event.results.length - 1];
      const text = last?.[0]?.transcript?.trim();
      if (!text) return;

      const norm = text.toLowerCase().replace(/[^\w\s]/g, "").trim();

      // 1) If user said the wake word, arm the assistant
      const isWake = WAKE_WORDS.some((w) => norm === w || norm.startsWith(w + " "));
      if (isWake) {
        armedUntilMs = Date.now() + ARM_WINDOW_MS;

        // If there is content after wake word, treat it as the command right away
        let remainder = norm;
        for (const w of WAKE_WORDS.sort((a, b) => b.length - a.length)) {
          if (remainder.startsWith(w)) {
            remainder = remainder.slice(w.length).trim();
            break;
          }
        }

        transcriptCallback?.(text);
        await speak(remainder ? "Understood." : "Yes?");
        if (remainder) {
          await sendToServer(remainder);
        } else {
          setState("listening");
        }
        return;
      }

      // 2) If not armed, ignore and stay idle (prevents random browsing)
      if (Date.now() > armedUntilMs) {
        setState("idle");
        return;
      }

      // 3) Armed: treat speech as command
      transcriptCallback?.(text);
      await sendToServer(text);
    };

    recognition.onerror = (e: any) => {
      console.warn("[webvoice] recognition error", e);
      const msg = e?.error || e?.message || "Speech recognition error";
      // Prevent endless spam when STT backend is unreachable.
      if (msg === "network") {
        fatalSttError = "network";
        recognitionEnabled = false;
        useGeminiStt = true;
        transcriptCallback?.(
          "Apologies, sir. The browser’s speech service is currently unavailable. "
          + "I will use the local microphone recording mode instead."
        );
        transcriptCallback?.(
          "மன்னிக்கவும் ஐயா, உலாவியின் குரல் சேவை தற்போது கிடைக்கவில்லை. "
          + "இதற்கு மாற்றாக பதிவு முறையை பயன்படுத்துகிறேன்."
        );
        try { recognition?.stop(); } catch {}
        setState("idle");
        return;
      }
      if (msg === "aborted") {
        // Common when toggling quickly; don't spam.
        setState("idle");
        return;
      }
      transcriptCallback?.(`[voice error] ${msg}`);
      if (!muted) setState("idle");
    };

    recognition.onend = () => {
      recognitionStarting = false;
      if (muted) return;
      if (!recognitionEnabled) return;
      if (fatalSttError) return;

      // Chrome ends recognition periodically; auto-restart with backoff.
      const delay = restartDelayMs;
      restartDelayMs = Math.min(restartDelayMs * 2, 8000);
      setTimeout(() => {
        if (muted || !recognitionEnabled || fatalSttError) return;
        tryStartRecognition();
      }, delay);
    };
  }

  function tryStartRecognition() {
    if (!recognition) return;
    if (recognitionStarting) return;
    recognitionStarting = true;
    try {
      recognition.start();
      restartDelayMs = 500;
    } catch (e) {
      recognitionStarting = false;
      // Ignore "already started" / invalid state.
    }
  }

  async function speak(text: string) {
    if (!text) return;
    setState("speaking");
    return await new Promise<void>((resolve) => {
      const hasTamil = /[\u0B80-\u0BFF]/.test(text);

      // Tamil: prefer backend Edge TTS for reliability (many browsers lack Tamil voices)
      if (hasTamil) {
        (async () => {
          try {
            const res = await fetch("http://localhost:8340/api/tts", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ text, lang: "ta" }),
            });
            const data = await res.json();
            const b64 = String(data?.audio_base64 || "");
            if (!b64) throw new Error(String(data?.error || "tts_failed"));
            const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
            const blob = new Blob([bytes], { type: data?.mime_type || "audio/mpeg" });
            const url = URL.createObjectURL(blob);
            const audio = new Audio(url);
            audio.onended = () => {
              URL.revokeObjectURL(url);
              if (!muted) setState(Date.now() <= armedUntilMs ? "listening" : "idle");
              resolve();
            };
            audio.onerror = () => {
              URL.revokeObjectURL(url);
              if (!muted) setState("idle");
              resolve();
            };
            await audio.play();
          } catch {
            // Fallback to browser TTS if backend fails
            const u = new SpeechSynthesisUtterance(text);
            u.lang = "ta-IN";
            u.rate = 1.0;
            u.pitch = 1.0;
            u.onend = () => {
              if (!muted) setState(Date.now() <= armedUntilMs ? "listening" : "idle");
              resolve();
            };
            u.onerror = () => {
              if (!muted) setState("idle");
              resolve();
            };
            window.speechSynthesis.cancel();
            window.speechSynthesis.speak(u);
          }
        })();
        return;
      }

      // English: browser TTS is typically fine
      const u = new SpeechSynthesisUtterance(text);
      u.lang = "en-GB";
      u.rate = 1.0;
      u.pitch = 1.0;
      u.onend = () => {
        if (!muted) setState(Date.now() <= armedUntilMs ? "listening" : "idle");
        resolve();
      };
      u.onerror = () => {
        if (!muted) setState("idle");
        resolve();
      };
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(u);
    });
  }

  async function sendToServer(text: string) {
    setState("thinking");
    try {
      const res = await fetch("http://localhost:8340/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const data = await res.json();
      const reply = (data?.text || "").toString();
      if (reply) {
        transcriptCallback?.(reply);
        await speak(reply);
      } else {
        if (!muted) setState("listening");
      }
    } catch (e) {
      console.warn("[webvoice] chat failed", e);
      await speak("Server connection failed.");
    }
  }

  async function startGeminiAlwaysOn() {
    if (!micStream) {
      await ensureMicAnalyser();
    }
    if (!micStream) throw new Error("Microphone not available");
    if (geminiLoopActive) return;
    geminiLoopActive = true;
    transcriptCallback?.("[mic] Always-on mode enabled. Say “Shadow …” or “Hey Shadow …”.");
    transcriptCallback?.("மைக்ரோஃபோன் செயல்பாட்டில் உள்ளது. “Shadow …” அல்லது “Hey Shadow …” என்று சொல்லுங்கள்.");

    const preferred = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/ogg"];
    const mime = preferred.find((m) => (window as any).MediaRecorder?.isTypeSupported?.(m)) || "";
    recordingMime = mime || "audio/webm";

    const buf = analyserNode ? new Uint8Array(analyserNode.fftSize) : null;

    async function startUtterance() {
      if (!geminiLoopActive) return;
      recordChunks = [];
      recorder = new MediaRecorder(micStream!, mime ? { mimeType: mime } : undefined);
      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) recordChunks.push(e.data);
      };
      recorder.onstop = async () => {
        if (!geminiLoopActive) return;
        try {
          const blob = new Blob(recordChunks, { type: recordingMime });
          if (blob.size < 900) {
            // Too short / silence; restart.
            geminiSpeechStarted = false;
            geminiSilenceMs = 0;
            startUtterance();
            return;
          }
          setState("thinking");
          const b64 = await blobToBase64(blob);
          const res = await fetch("http://localhost:8340/api/stt", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ audio_base64: b64, mime_type: recordingMime }),
          });
          const data = await res.json();
          const transcript = (data?.text || "").toString().trim();
          if (!transcript) {
            setState("idle");
            geminiSpeechStarted = false;
            geminiSilenceMs = 0;
            startUtterance();
            return;
          }

          // Only act when wake word is present.
          const norm = transcript.toLowerCase().replace(/[^\w\s]/g, " ").replace(/\s+/g, " ").trim();
          const wake = WAKE_WORDS.find((w) => norm === w || norm.startsWith(w + " "));
          if (!wake) {
            // Ignore background speech.
            setState("idle");
            geminiSpeechStarted = false;
            geminiSilenceMs = 0;
            startUtterance();
            return;
          }

          // Strip wake word and execute remainder.
          let remainder = norm;
          remainder = remainder.startsWith(wake) ? remainder.slice(wake.length).trim() : remainder;
          transcriptCallback?.(transcript);
          armedUntilMs = Date.now() + ARM_WINDOW_MS;
          if (!remainder) {
            await speak(/[\u0B80-\u0BFF]/.test(transcript) ? "ஆம், ஐயா?" : "Yes, sir?");
            setState("idle");
            geminiSpeechStarted = false;
            geminiSilenceMs = 0;
            startUtterance();
            return;
          }
          await sendToServer(remainder);
          setState("idle");
          geminiSpeechStarted = false;
          geminiSilenceMs = 0;
          startUtterance();
        } catch {
          setState("idle");
          geminiSpeechStarted = false;
          geminiSilenceMs = 0;
          startUtterance();
        }
      };
      recorder.start(250);
      geminiSpeechStarted = false;
      geminiSilenceMs = 0;
      setState("idle");
    }

    // Silence detection loop to auto-stop after speaking.
    const loop = setInterval(() => {
      if (!geminiLoopActive) {
        clearInterval(loop);
        return;
      }
      if (!analyserNode || !buf) return;
      try {
        analyserNode.getByteTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) {
          const v = (buf[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / buf.length);
        const speaking = rms > 0.01;
        if (speaking) {
          geminiSpeechStarted = true;
          geminiSilenceMs = 0;
          setState("listening");
        } else if (geminiSpeechStarted) {
          geminiSilenceMs += 100;
          // stop after ~900ms of silence following speech
          if (geminiSilenceMs >= 900) {
            try {
              if (recorder && recorder.state !== "inactive") recorder.stop();
            } catch {}
            geminiSpeechStarted = false;
            geminiSilenceMs = 0;
          }
        }
      } catch {
        // ignore
      }
    }, 100);

    // Start first utterance recorder
    startUtterance();
  }

  function stopGeminiAlwaysOn() {
    geminiLoopActive = false;
    try {
      if (recorder && recorder.state !== "inactive") recorder.stop();
    } catch {}
    recorder = null;
    recordChunks = [];
  }

  function blobToBase64(blob: Blob): Promise<string> {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onerror = () => reject(new Error("read failed"));
      r.onload = () => {
        const s = String(r.result || "");
        const comma = s.indexOf(",");
        resolve(comma >= 0 ? s.slice(comma + 1) : s);
      };
      r.readAsDataURL(blob);
    });
  }

  return {
    async connect() {
      await ensureMicAnalyser();
      ensureRecognition();
      if (!recognition) {
        // No browser STT; use Gemini STT fallback.
        useGeminiStt = true;
      }
      // Do not auto-start STT; the UI mic button controls it.
      muted = true;
      recognitionEnabled = false;
      setState("idle");
    },
    disconnect() {
      muted = true;
      try {
        recognition?.stop();
      } catch {}
      recognition = null;
      recognitionEnabled = false;
      fatalSttError = null;
      if (micStream) {
        for (const t of micStream.getTracks()) t.stop();
      }
      micStream = null;
      analyserNode = undefined;
    },
    async setMute(m: boolean) {
      muted = m;
      if (muted) {
        setState("idle");
        recognitionEnabled = false;
        try {
          recognition?.stop();
        } catch {}
        stopGeminiAlwaysOn();
        window.speechSynthesis.cancel();
      } else {
        fatalSttError = null;
        armedUntilMs = Date.now() + ARM_WINDOW_MS;
        if (useGeminiStt) {
          await startGeminiAlwaysOn();
        } else {
          ensureRecognition();
          recognitionEnabled = true;
          tryStartRecognition();
          setState("idle");
        }
      }
    },
    async sendData(data: any) {
      // Used by chat input: { type: "transcript", text: "..." }
      const text = data?.text?.toString?.() || "";
      if (!text) return;
      transcriptCallback?.(text);
      // Chat input doesn't need wake word
      armedUntilMs = Date.now() + ARM_WINDOW_MS;
      await sendToServer(text);
    },
    getAnalyser() {
      return analyserNode;
    },
    onTranscript(cb) {
      transcriptCallback = cb;
    },
    onStateChange(cb) {
      stateCallback = cb;
    },
  };
}
