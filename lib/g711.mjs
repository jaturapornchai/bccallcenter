// G.711 mu-law (PCMU) <-> Linear PCM16 encoder/decoder (8kHz mono)

const MULAW_MAX = 0x1fff;
const MULAW_BIAS = 33;

const decodeTable = new Int16Array(256);
for (let i = 0; i < 256; i++) {
  let mu = ~i & 0xff;
  const sign = mu & 0x80;
  const exponent = (mu >> 4) & 0x07;
  const mantissa = mu & 0x0f;
  let sample = ((mantissa << 3) + MULAW_BIAS) << exponent;
  sample -= MULAW_BIAS;
  decodeTable[i] = sign ? -sample : sample;
}

export function decodeMuLawSample(muByte) {
  return decodeTable[muByte & 0xff];
}

export function encodeMuLawSample(pcm16) {
  let sample = pcm16;
  let sign = 0;
  if (sample < 0) {
    sign = 0x80;
    sample = -sample;
  }
  if (sample > MULAW_MAX) sample = MULAW_MAX;
  sample += MULAW_BIAS;

  let exponent = 7;
  for (let expMask = 0x4000; (sample & expMask) === 0 && exponent > 0; exponent--, expMask >>= 1) {}
  const mantissa = (sample >> (exponent + 3)) & 0x0f;
  return ~(sign | (exponent << 4) | mantissa) & 0xff;
}

export function decodeMuLawBuffer(buf) {
  const out = new Int16Array(buf.length);
  for (let i = 0; i < buf.length; i++) {
    out[i] = decodeTable[buf[i]];
  }
  return out;
}

export function encodeMuLawBuffer(int16Arr) {
  const out = Buffer.allocUnsafe(int16Arr.length);
  for (let i = 0; i < int16Arr.length; i++) {
    out[i] = encodeMuLawSample(int16Arr[i]);
  }
  return out;
}
