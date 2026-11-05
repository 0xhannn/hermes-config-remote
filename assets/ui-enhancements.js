/* Hermes UI Common Enhancements - website-design-ops */
(function() {
  let audioCtx = null;

  function getAudioContext() {
    if (!audioCtx) {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (AudioContextClass) {
        audioCtx = new AudioContextClass();
      }
    }
    if (audioCtx && audioCtx.state === 'suspended') {
      audioCtx.resume();
    }
    return audioCtx;
  }

  // Pre-unlock audio on any initial interaction
  const unlockEvents = ['pointerdown', 'touchstart', 'mousedown', 'keydown'];
  function unlockAudio() {
    getAudioContext();
    unlockEvents.forEach(evt => document.removeEventListener(evt, unlockAudio));
  }
  unlockEvents.forEach(evt => document.addEventListener(evt, unlockAudio, { passive: true, once: true }));

  function playClickSound(type = 'click') {
    try {
      const ctx = getAudioContext();
      if (!ctx) return;

      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);

      const now = ctx.currentTime;
      if (type === 'click') {
        // Renyah tactile mechanical pop click
        osc.type = 'triangle';
        osc.frequency.setValueAtTime(1200, now);
        osc.frequency.exponentialRampToValueAtTime(300, now + 0.04);
        gain.gain.setValueAtTime(0.18, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.04);
        osc.start(now);
        osc.stop(now + 0.04);
      } else if (type === 'confirm') {
        // Nada konfirmasi positif
        osc.type = 'sine';
        osc.frequency.setValueAtTime(440, now);
        osc.frequency.exponentialRampToValueAtTime(880, now + 0.08);
        gain.gain.setValueAtTime(0.2, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.08);
        osc.start(now);
        osc.stop(now + 0.08);
      }
    } catch (e) {}
  }

  window.playClickSound = playClickSound;

  // Tangkap pada capture phase agar tidak kalah cepat oleh preventDefault / stopPropagation
  document.addEventListener('pointerdown', (e) => {
    const target = e.target.closest('button, .btn, a, .clickable, .hub-card, .model-picker-option, .provider-card, .tree-leaf, input[type="submit"]');
    if (target) {
      if (target.classList.contains('btn-primary') || target.id === 'confirmBtnOk') {
        playClickSound('confirm');
      } else {
        playClickSound('click');
      }
    }
  }, true);
})();
