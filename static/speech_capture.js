class SpeechCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(4096);
    this.used = 0;
    this.done = false;
    this.port.onmessage = ({ data }) => {
      if (data === "finish") {
        if (this.used) this.port.postMessage(this.buffer.slice(0, this.used));
        this.done = true;
        this.port.postMessage("finished");
      }
    };
  }

  process(inputs) {
    if (this.done) return false;
    const channel = inputs[0]?.[0];
    if (channel) {
      for (const sample of channel) {
        this.buffer[this.used++] = sample;
        if (this.used === this.buffer.length) {
          this.port.postMessage(this.buffer);
          this.used = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor("ce-speech-capture", SpeechCapture);
