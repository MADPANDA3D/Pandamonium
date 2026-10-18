// Track indexes belong to one loaded stream. Preferences store languages,
// never indexes, so switching episodes cannot select an unrelated language.
const LANGUAGE_ALIASES = {
  eng: 'en', ita: 'it', jpn: 'ja', spa: 'es', fra: 'fr', fre: 'fr',
  deu: 'de', ger: 'de', por: 'pt', rus: 'ru', zho: 'zh', chi: 'zh',
  kor: 'ko', ara: 'ar', hin: 'hi', nld: 'nl', dut: 'nl', pol: 'pl',
};

export function trackLanguage(value) {
  const code = String(value || '').trim().toLowerCase().split(/[-_]/)[0];
  return LANGUAGE_ALIASES[code] || code;
}

function label(track, fallback) {
  if (track.name || track.label) return track.name || track.label;
  const language = trackLanguage(track.lang || track.language);
  if (language) {
    try { return new Intl.DisplayNames(['en'], { type: 'language' }).of(language); } catch (_) {}
  }
  return fallback;
}

export function audioOptions(slot) {
  const hls = slot?.hls;
  const tracks = hls ? hls.audioTracks : Array.from(slot?.video?.audioTracks || []);
  return (tracks || []).map((track, index) => ({
    value: `${hls ? 'hls' : 'native'}:${index}`,
    label: label(track, `Audio ${index + 1}`),
    language: trackLanguage(track.lang || track.language),
    index, track,
    selected: hls ? hls.audioTrack === index : Boolean(track.enabled),
  }));
}

export function subtitleOptions(slot) {
  const hlsTracks = slot?.hls?.subtitleTracks || [];
  const options = hlsTracks.map((track, index) => ({
    value: `hls:${index}`, label: label(track, `Subtitles ${index + 1}`),
    language: trackLanguage(track.lang), index, track,
    selected: slot.hls.subtitleDisplay && slot.hls.subtitleTrack === index,
  }));
  for (const [index, track] of Array.from(slot?.video?.textTracks || []).entries()) {
    if (!['subtitles', 'captions'].includes(track.kind)) continue;
    const language = trackLanguage(track.language);
    // HLS creates native TextTracks too; avoid listing those and the CLI's
    // duplicate external subtitle stub a second time.
    if (hlsTracks.some(item => language && trackLanguage(item.lang) === language)) continue;
    options.push({ value: `native:${index}`, label: label(track, `Subtitles ${index + 1}`),
      language, index, track, selected: track.mode === 'showing' });
  }
  return options;
}

export function preferredTrack(options, language, name = '') {
  const matches = options.filter(option => option.language === trackLanguage(language));
  return matches.find(option => option.label === name) || matches[0] || null;
}

export function selectAudio(slot, option) {
  if (!option) return;
  if (option.value.startsWith('hls:')) {
    if (slot.hls.audioTrack !== option.index) slot.hls.audioTrack = option.index;
  } else {
    for (const track of Array.from(slot.video.audioTracks || [])) track.enabled = track === option.track;
  }
}

export function selectSubtitle(slot, option) {
  const hlsOption = option?.value.startsWith('hls:');
  // Clear external tracks before HLS enables its own selected TextTrack.
  // Otherwise switching from external subtitles can leave two tracks showing.
  for (const track of Array.from(slot.video.textTracks || [])) {
    if (['subtitles', 'captions'].includes(track.kind)) {
      const mode = !hlsOption && track === option?.track ? 'showing' : 'disabled';
      if (track.mode !== mode) track.mode = mode;
    }
  }
  if (slot.hls) {
    slot.hls.subtitleDisplay = Boolean(hlsOption);
    const index = hlsOption ? option.index : -1;
    if (slot.hls.subtitleTrack !== index) slot.hls.subtitleTrack = index;
  }
}
