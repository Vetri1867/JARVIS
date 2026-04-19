/**
 * Voice input (Web Speech API) and audio output (AudioContext) for SHADOW.
 */

// ---------------------------------------------------------------------------
// Speech Recognition
// ---------------------------------------------------------------------------

export interface VoiceInput {
  start(): void;
  stop(): void;
  pause(): void;
  resume(): void;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
declare const webkitSpeechRecognition: any;

export function createVoiceInput(
  onTranscript: (text: string) => void,
  onError: (msg: string) => void,
  onWake?: () => void,
  onSleep?: () => void
): VoiceInput {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const SR = (window as any).SpeechRecognition || (typeof webkitSpeechRecognition !== "undefined" ? webkitSpeechRecognition : null);
  if (!SR) {
    onError("Speech recognition not supported in this browser");
    return { start() {}, stop() {}, pause() {}, resume() {} };
  }

  const recognition = new SR();
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.lang = "en-US";

  let shouldListen = false;
  let paused = false;
  let isAwake = false;
  let awakeTimeout: any = null;

  const WAKE_WORDS = ["shadow", "sadow", "shallow", "shatter", "chateau", "shaddo", "shadows"];

  recognition.onresult = (event: any) => {
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i];
      const text = result[0].transcript.trim();
      const lowerText = text.toLowerCase().replace(/[.,!?]/g, "");

      if (!result.isFinal) {
        // Fast Wake Word Detection on interim results
        if (!isAwake) {
          const parts = lowerText.split(/\s+/);
          if (parts.some((p: string) => WAKE_WORDS.includes(p))) {
            isAwake = true;
            console.log("[SHADOW] woke up (interim)!");
            if (onWake) onWake();
            if (awakeTimeout) clearTimeout(awakeTimeout);
          }
        }
        continue;
      }

      if (text) {
        console.log("[HEARD]:", text);
        
        let triggered = false;
        let command = "";

        // Check if this final result contains the wake word (if not already awake)
        if (!isAwake) {
          const parts = lowerText.split(/\s+/);
          const wwIndex = parts.findIndex((p: string) => WAKE_WORDS.includes(p));
          if (wwIndex !== -1) {
            triggered = true;
            const wwUsed = parts[wwIndex];
            const regex = new RegExp(`\\b${wwUsed}\\b`, 'i');
            const match = text.match(regex);
            if (match) {
              command = text.substring(match.index! + match[0].length).trim();
            }
          }
        }

        if (triggered || isAwake) {
          if (!isAwake) {
            isAwake = true;
            console.log("[SHADOW] woke up!");
            if (onWake) onWake();
          }
          
          if (awakeTimeout) clearTimeout(awakeTimeout);
          
          let textToSend = triggered ? command : text;
          textToSend = textToSend.replace(/^[.,!?]\s*/, "").trim();

          if (textToSend) {
            isAwake = false;
            if (onSleep) onSleep();
            onTranscript(textToSend);
          } else {
            // Just woke up, waiting for command
            awakeTimeout = setTimeout(() => {
              isAwake = false;
              if (onSleep) onSleep();
              console.log("[SHADOW] went to sleep due to inactivity.");
            }, 10000);
          }
        }
      }
    }
  };

  recognition.onend = () => {
    if (shouldListen && !paused) {
      try {
        recognition.start();
      } catch {
        // Already started
      }
    }
  };

  recognition.onerror = (event: any) => {
    console.error("[voice] recognition error:", event.error, event.message);
    if (event.error === "not-allowed") {
      onError("Microphone access denied. Please check site permissions.");
      shouldListen = false;
    } else if (event.error === "network") {
      onError("Speech recognition network error. check connection.");
    } else if (event.error === "no-speech") {
      // Normal, just restart in onend
    } else if (event.error === "aborted") {
      // Expected during pause
    } else {
      console.warn("[voice] unexpected recognition error:", event.error);
    }
  };

  recognition.onstart = () => {
    console.log("[voice] recognition started");
  };

  return {
    start() {
      console.log("[voice] starting recognition...");
      shouldListen = true;
      paused = false;
      try {
        recognition.start();
      } catch (e) {
        console.warn("[voice] start failed (likely already running):", e);
      }
    },
    stop() {
      console.log("[voice] stopping recognition...");
      shouldListen = false;
      paused = false;
      recognition.stop();
    },
    pause() {
      console.log("[voice] pausing recognition...");
      paused = true;
      recognition.stop();
    },
    resume() {
      console.log("[voice] resuming recognition...");
      paused = false;
      if (shouldListen) {
        try {
          recognition.start();
        } catch (e) {
          console.warn("[voice] resume failed:", e);
        }
      }
    },
  };
}

// ---------------------------------------------------------------------------
// Audio Player
// ---------------------------------------------------------------------------

export interface AudioPlayer {
  enqueue(base64: string): Promise<void>;
  stop(): void;
  getAnalyser(): AnalyserNode;
  onFinished(cb: () => void): void;
}

export function createAudioPlayer(): AudioPlayer {
  const audioCtx = new AudioContext();
  const analyser = audioCtx.createAnalyser();
  analyser.fftSize = 256;
  analyser.smoothingTimeConstant = 0.8;
  analyser.connect(audioCtx.destination);

  const queue: AudioBuffer[] = [];
  let isPlaying = false;
  let currentSource: AudioBufferSourceNode | null = null;
  let finishedCallback: (() => void) | null = null;

  function playNext() {
    if (queue.length === 0) {
      isPlaying = false;
      currentSource = null;
      finishedCallback?.();
      return;
    }

    isPlaying = true;
    const buffer = queue.shift()!;
    const source = audioCtx.createBufferSource();
    source.buffer = buffer;
    source.connect(analyser);
    currentSource = source;

    source.onended = () => {
      if (currentSource === source) {
        playNext();
      }
    };

    source.start();
  }

  return {
    async enqueue(base64: string) {
      // Resume audio context (browser autoplay policy)
      if (audioCtx.state === "suspended") {
        await audioCtx.resume();
      }

      try {
        const binary = atob(base64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) {
          bytes[i] = binary.charCodeAt(i);
        }
        const audioBuffer = await audioCtx.decodeAudioData(bytes.buffer.slice(0));
        queue.push(audioBuffer);
        if (!isPlaying) playNext();
      } catch (err) {
        console.error("[audio] decode error:", err);
        // Skip bad audio, continue
        if (!isPlaying && queue.length > 0) playNext();
      }
    },

    stop() {
      queue.length = 0;
      if (currentSource) {
        try {
          currentSource.stop();
        } catch {
          // Already stopped
        }
        currentSource = null;
      }
      isPlaying = false;
    },

    getAnalyser() {
      return analyser;
    },

    onFinished(cb: () => void) {
      finishedCallback = cb;
    },
  };
}
