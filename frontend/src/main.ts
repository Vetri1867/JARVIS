/**
 * SHADOW — Main entry point (LiveKit Edition).
 *
 * Wires together the orb visualization, LiveKit communication,
 * and UI controls into a single experience.
 */

import { createOrb, type OrbState } from "./orb";
import { createWebVoice } from "./voice";
import { createSocket } from "./ws";
import { openSettings, checkFirstTimeSetup } from "./settings";
import "./style.css";

// ---------------------------------------------------------------------------
// State machine
// ---------------------------------------------------------------------------

type State = "idle" | "listening" | "thinking" | "speaking";
let currentState: State = "idle";
let isMuted = true; // start with mic OFF to avoid "press screen" requirement

const statusEl = document.getElementById("status-text")!;
const errorEl = document.getElementById("error-text")!;
const memoryFeed = document.getElementById("memory-feed")!;
const tasksFeed = document.getElementById("tasks-feed")!;
const netStatusEl = document.getElementById("net-status");

function updateFeed(el: HTMLElement, text: string) {
  const div = document.createElement("div");
  div.style.marginBottom = "8px";
  div.style.borderLeft = "1px solid rgba(0, 212, 255, 0.3)";
  div.style.paddingLeft = "8px";
  div.textContent = `> ${text}`;
  el.prepend(div);
  if (el.children.length > 5) el.lastChild?.remove();
}

function updateSystemStats() {
  const cpu = Math.floor(Math.random() * 20) + 5;
  const mem = Math.floor(Math.random() * 30) + 40;
  document.getElementById("cpu-load")!.textContent = `${cpu}%`;
  document.getElementById("mem-usage")!.textContent = `${mem}%`;
}
setInterval(updateSystemStats, 3000);
updateSystemStats();

function showError(msg: string) {
  errorEl.textContent = msg;
  errorEl.style.opacity = "1";
  setTimeout(() => {
    errorEl.style.opacity = "0";
  }, 5000);
}

function updateStatus(state: State) {
  const labels: Record<State, string> = {
    idle: "waiting for wake word...",
    listening: "listening...",
    thinking: "thinking...",
    speaking: "",
  };
  statusEl.textContent = labels[state];
}

// ---------------------------------------------------------------------------
// Init components
// ---------------------------------------------------------------------------

const canvas = document.getElementById("orb-canvas") as HTMLCanvasElement;
const orb = createOrb(canvas);

// Still need the socket for task notifications and stats
const wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
// Use backend port directly (works even without Vite proxy)
const WS_URL = `${wsProto}//localhost:8340/ws/voice`;
const socket = createSocket(WS_URL);

const voice = createWebVoice();

function transition(newState: State) {
  if (newState === currentState) return;
  currentState = newState;
  orb.setState(newState as OrbState);
  updateStatus(newState);
}

// ---------------------------------------------------------------------------
// LiveKit Event Handlers
// ---------------------------------------------------------------------------

voice.onTranscript((text) => {
  updateFeed(memoryFeed, text);
});

voice.onStateChange((state) => {
  transition(state);
});

// Periodic check to attach analyser once track is active
const analyserInterval = setInterval(() => {
  const node = voice.getAnalyser();
  if (node) {
    orb.setAnalyser(node);
    clearInterval(analyserInterval);
    console.log("[main] orb linked to livekit analyser");

    // MIC level indicator (proves audio is reaching SHADOW even if STT fails)
    if (netStatusEl) {
      const buf = new Uint8Array(node.fftSize);
      setInterval(() => {
        try {
          node.getByteTimeDomainData(buf);
          // RMS in [0..~1]
          let sum = 0;
          for (let i = 0; i < buf.length; i++) {
            const v = (buf[i] - 128) / 128;
            sum += v * v;
          }
          const rms = Math.sqrt(sum / buf.length);
          // Lower threshold to detect quiet microphones.
          if (rms > 0.006) {
            netStatusEl.textContent = "MIC_ON";
            netStatusEl.classList.add("status-online");
          } else {
            netStatusEl.textContent = "MIC_IDLE";
          }
        } catch {
          // ignore
        }
      }, 300);
    }
  }
}, 500);

// ---------------------------------------------------------------------------
// Kick off
// ---------------------------------------------------------------------------

async function startAssistant() {
  try {
    await voice.connect("", "");
    transition("idle");
    if (netStatusEl) {
      netStatusEl.textContent = "ONLINE";
      netStatusEl.classList.add("status-online");
    }
  } catch (err: any) {
    console.error("[assistant] failed to start:", err);
    showError(`Voice init failed: ${err?.message || err}. Click the page and allow microphone.`);
    if (netStatusEl) netStatusEl.textContent = "ERROR";
  }
}

// Start backend connection immediately. Mic is enabled via the mic button.
startAssistant();
statusEl.textContent = "click mic to enable voice...";

// ---------------------------------------------------------------------------
// UI Controls
// ---------------------------------------------------------------------------

const btnMute = document.getElementById("btn-mute")!;
const btnMenu = document.getElementById("btn-menu")!;
const menuDropdown = document.getElementById("menu-dropdown")!;
const btnRestart = document.getElementById("btn-restart")!;
const btnFixSelf = document.getElementById("btn-fix-self")!;

btnMute.addEventListener("click", async (e) => {
  e.stopPropagation();
  isMuted = !isMuted;
  btnMute.classList.toggle("muted", isMuted);
  await voice.setMute(isMuted);
  if (isMuted) {
    transition("idle");
  } else {
    transition("idle");
  }
});

// Ensure initial UI reflects muted state
btnMute.classList.toggle("muted", isMuted);

btnMenu.addEventListener("click", (e) => {
  e.stopPropagation();
  menuDropdown.style.display = menuDropdown.style.display === "none" ? "block" : "none";
});

document.addEventListener("click", () => {
  menuDropdown.style.display = "none";
});

btnRestart.addEventListener("click", async (e) => {
  e.stopPropagation();
  menuDropdown.style.display = "none";
  statusEl.textContent = "restarting...";
  try {
    await fetch("/api/restart", { method: "POST" });
    setTimeout(() => window.location.reload(), 4000);
  } catch {
    statusEl.textContent = "restart failed";
  }
});

btnFixSelf.addEventListener("click", (e) => {
  e.stopPropagation();
  menuDropdown.style.display = "none";
  socket.send({ type: "fix_self" });
  statusEl.textContent = "entering work mode...";
});

// Self-repair responses from backend
socket.onMessage((msg) => {
  const type = String((msg as any)?.type || "");
  if (type === "text") {
    updateFeed(tasksFeed, String((msg as any)?.text || ""));
  }
  if (type === "status") {
    const state = String((msg as any)?.state || "");
    if (state === "idle" || state === "listening" || state === "thinking" || state === "speaking") {
      transition(state as any);
    }
  }
});

// Settings button
const btnSettings = document.getElementById("btn-settings")!;
btnSettings.addEventListener("click", (e) => {
  e.stopPropagation();
  menuDropdown.style.display = "none";
  openSettings();
});

// Chat Input logic
const chatInput = document.getElementById("chat-input") as HTMLInputElement;
const btnSendChat = document.getElementById("btn-send-chat")!;

function handleChatSubmit() {
  const text = chatInput.value.trim();
  if (!text) return;
  
  voice.sendData({ type: "transcript", text, isFinal: true });
  
  chatInput.value = "";
  transition("thinking");
}

btnSendChat.addEventListener("click", handleChatSubmit);
chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    handleChatSubmit();
  }
});

// First-time setup detection
setTimeout(() => {
  checkFirstTimeSetup();
}, 2000);
