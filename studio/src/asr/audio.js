// Decode an audio file (mp4/AAC, wav, …) with the browser's own decoder and resample it
// to the 16 kHz mono samples speech models expect.
export async function decodeTo16k(blob) {
  const buf = await blob.arrayBuffer();
  const ctx = new AudioContext();
  try {
    const audio = await ctx.decodeAudioData(buf);
    const frames = Math.ceil(audio.duration * 16000);
    const off = new OfflineAudioContext(1, Math.max(1, frames), 16000);
    const src = off.createBufferSource();
    src.buffer = audio; src.connect(off.destination); src.start();
    return (await off.startRendering()).getChannelData(0);
  } finally {
    ctx.close();
  }
}
