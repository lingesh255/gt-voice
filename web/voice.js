const TARGET_RATE = 16000;

export class Mic {
  constructor({ onSpeechStart, onUtterance, onLevel }) {
    this.onSpeechStart = onSpeechStart;
    this.onUtterance = onUtterance;
    this.onLevel = onLevel;
    this.mode = "handsfree";
    this.enabled = false;
    this.agentSpeaking = false;
    this.bargeIn = true;
    this.recording = false;
    this.pushHeld = false;
    this.floor = 0.01;
    this.preRoll = [];
    this.chunks = [];
    this.loudMs = 0;
    this.quietMs = 0;
    this.spokenMs = 0;
  }

  async start() {
    if (this.context) return;
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    this.context = new AudioContext();
    await this.context.audioWorklet.addModule("/static/recorder-worklet.js");
    const source = this.context.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.context, "recorder");
    const sink = this.context.createGain();
    sink.gain.value = 0;
    source.connect(this.node);
    this.node.connect(sink);
    sink.connect(this.context.destination);
    this.rate = this.context.sampleRate;
    this.node.port.onmessage = (event) => this.frame(event.data);
  }

  stop() {
    this.enabled = false;
    this.recording = false;
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    if (this.context) this.context.close();
    this.context = null;
    this.stream = null;
  }

  threshold() {
    const base = Math.max(0.012, this.floor * 3);
    return this.agentSpeaking ? base * 2.5 : base;
  }

  frame(samples) {
    let sum = 0;
    for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
    const level = Math.sqrt(sum / samples.length);
    const ms = (samples.length / this.rate) * 1000;
    if (this.onLevel) this.onLevel(level);

    if (this.recording) {
      this.chunks.push(samples);
      this.spokenMs += ms;
      if (this.mode === "push") {
        if (!this.pushHeld) this.finish();
        return;
      }
      if (level < this.threshold() * 0.6) this.quietMs += ms;
      else this.quietMs = 0;
      if (this.quietMs >= 750 || this.spokenMs >= 20000) this.finish();
      return;
    }

    this.preRoll.push(samples);
    while (this.preRoll.length * ms > 400) this.preRoll.shift();
    if (!this.enabled) return;

    if (this.mode === "push") {
      if (this.pushHeld) this.begin();
      return;
    }
    if (this.agentSpeaking && !this.bargeIn) return;
    if (level > this.threshold()) {
      this.loudMs += ms;
      if (this.loudMs >= (this.agentSpeaking ? 250 : 120)) this.begin();
    } else {
      this.loudMs = 0;
      this.floor = this.floor * 0.95 + level * 0.05;
    }
  }

  begin() {
    this.recording = true;
    this.chunks = [...this.preRoll];
    this.preRoll = [];
    this.quietMs = 0;
    this.loudMs = 0;
    this.spokenMs = 0;
    if (this.onSpeechStart) this.onSpeechStart();
  }

  finish() {
    this.recording = false;
    const chunks = this.chunks;
    this.chunks = [];
    if (this.spokenMs < 350) return;
    const merged = merge(chunks);
    this.onUtterance(encodeWav(resample(merged, this.rate, TARGET_RATE), TARGET_RATE));
  }
}

function merge(chunks) {
  const length = chunks.reduce((n, c) => n + c.length, 0);
  const out = new Float32Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.length;
  }
  return out;
}

function resample(input, from, to) {
  if (from === to) return input;
  const ratio = from / to;
  const length = Math.floor(input.length / ratio);
  const out = new Float32Array(length);
  for (let i = 0; i < length; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j++) sum += input[j];
    out[i] = end > start ? sum / (end - start) : input[start] || 0;
  }
  return out;
}

function encodeWav(samples, rate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const text = (offset, value) => [...value].forEach((ch, i) => view.setUint8(offset + i, ch.charCodeAt(0)));
  text(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([view], { type: "audio/wav" });
}

export class Speaker {
  constructor({ onStart, onEnd }) {
    this.onStart = onStart;
    this.onEnd = onEnd;
    this.audio = null;
    this.utterance = null;
  }

  play(reply) {
    this.stop();
    if (reply.audio) {
      const bytes = Uint8Array.from(atob(reply.audio), (c) => c.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], { type: reply.mime || "audio/wav" }));
      const audio = new Audio(url);
      this.audio = audio;
      const done = () => {
        URL.revokeObjectURL(url);
        if (this.audio === audio) {
          this.audio = null;
          this.onEnd();
        }
      };
      audio.onended = done;
      audio.onerror = done;
      this.onStart();
      audio.play().catch(done);
      return;
    }
    if (!reply.text || !("speechSynthesis" in window)) {
      this.onEnd();
      return;
    }
    const utterance = new SpeechSynthesisUtterance(reply.text);
    utterance.lang = reply.browser_language || reply.language || "en-IN";
    const voice = speechSynthesis.getVoices().find((v) => v.lang === utterance.lang)
      || speechSynthesis.getVoices().find((v) => v.lang.startsWith(utterance.lang.slice(0, 2)));
    if (voice) utterance.voice = voice;
    this.utterance = utterance;
    utterance.onend = () => {
      if (this.utterance === utterance) {
        this.utterance = null;
        this.onEnd();
      }
    };
    utterance.onerror = utterance.onend;
    this.onStart();
    speechSynthesis.speak(utterance);
  }

  stop() {
    if (this.audio) {
      const audio = this.audio;
      this.audio = null;
      audio.pause();
    }
    if (this.utterance) {
      this.utterance = null;
      speechSynthesis.cancel();
    }
  }

  get busy() {
    return Boolean(this.audio || this.utterance);
  }
}

export class BrowserListener {
  constructor({ onText, onError }) {
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    this.supported = Boolean(Recognition);
    this.onText = onText;
    this.onError = onError;
    this.active = false;
    if (!this.supported) return;
    this.recognition = new Recognition();
    this.recognition.continuous = false;
    this.recognition.interimResults = false;
    this.recognition.onresult = (event) => {
      const result = event.results[event.results.length - 1];
      if (result.isFinal) {
        const alt = result[0];
        this.onText(alt.transcript, alt.confidence);
      }
    };
    this.recognition.onerror = (event) => {
      if (event.error !== "no-speech" && event.error !== "aborted") this.onError(event.error);
    };
    this.recognition.onend = () => {
      this.running = false;
      if (this.active) this.listen(this.language);
    };
  }

  listen(language) {
    if (!this.supported) return;
    this.active = true;
    this.language = language;
    if (this.running) return;
    this.recognition.lang = language;
    try {
      this.recognition.start();
      this.running = true;
    } catch (error) {
      this.running = false;
    }
  }

  pause() {
    this.active = false;
    if (this.supported && this.running) this.recognition.abort();
  }
}
