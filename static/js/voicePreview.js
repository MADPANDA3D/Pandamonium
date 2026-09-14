export const VOICE_PREVIEW_TEXT = 'Hello — this is a voice preview from Pandamonium.';

export function startVoicePreview(options = {}) {
  const apiBase = options.apiBase || '';
  const text = String(options.text || VOICE_PREVIEW_TEXT);
  const provider = String(options.provider || '').trim().toLowerCase();
  const model = String(options.model || '').trim();
  const voice = String(options.voice || '').trim();
  const speed = Number(options.speed) || 1;
  const fetchImpl = options.fetchImpl || fetch;
  const onPhase = typeof options.onPhase === 'function' ? options.onPhase : () => {};
  let stopped = false;
  let audio = null;
  let objectUrl = '';

  const stop = () => {
    stopped = true;
    if (audio) {
      try { audio.pause(); } catch (_) {}
    }
    try { globalThis.window?.speechSynthesis?.cancel(); } catch (_) {}
  };

  const done = (async () => {
    if (provider === 'browser') {
      const synth = globalThis.window?.speechSynthesis;
      const Utterance = globalThis.SpeechSynthesisUtterance;
      if (!synth || typeof Utterance !== 'function') {
        throw new Error('This browser cannot play the voice preview. Choose a voice provider in Settings.');
      }
      const utterance = new Utterance(text);
      utterance.rate = speed;
      const wanted = voice.toLowerCase();
      if (wanted && typeof synth.getVoices === 'function') {
        const voices = synth.getVoices() || [];
        const match = voices.find(item => String(item.name).toLowerCase() === wanted)
          || voices.find(item => String(item.name).toLowerCase().includes(wanted));
        if (match) utterance.voice = match;
      }
      onPhase('playing');
      await new Promise((resolve, reject) => {
        utterance.onend = () => resolve();
        utterance.onerror = () => {
          if (stopped) resolve();
          else reject(new Error('The browser could not play the preview. Try another voice in Settings.'));
        };
        if (stopped) {
          resolve();
          return;
        }
        try {
          synth.speak(utterance);
        } catch (_) {
          reject(new Error('The browser could not play the preview. Try another voice in Settings.'));
        }
      });
      return;
    }

    onPhase('loading');
    const response = await fetchImpl(`${apiBase}/api/tts/synthesize`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text,
        format: 'audio',
        model,
        voice,
        speed: String(speed),
        use_cache: false,
      }),
    });
    if (!response.ok) {
      throw new Error("The voice preview couldn't be generated. Check the voice settings and try again.");
    }
    const blob = await response.blob();
    objectUrl = URL.createObjectURL(blob);
    audio = new Audio(objectUrl);
    audio.playbackRate = speed;
    onPhase('playing');
    await new Promise((resolve, reject) => {
      audio.onended = () => resolve();
      audio.onerror = () => {
        if (stopped) resolve();
        else reject(new Error('The preview could not play here. Check that this device can play audio, then try again.'));
      };
      if (stopped) {
        resolve();
        return;
      }
      audio.play().catch(() => {
        if (stopped) resolve();
        else reject(new Error('The preview could not play here. Check that this device can play audio, then try again.'));
      });
    });
  })().finally(() => {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
  });

  return { done, stop };
}

export default { startVoicePreview, VOICE_PREVIEW_TEXT };
