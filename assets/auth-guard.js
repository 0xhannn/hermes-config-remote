// Hermes auth guard — verifies session via API cookie (not localStorage).
// Returns a Promise<bool>; redirects to /hermes/auth/?next=... on 401.
async function hermesGuard(nextPath) {
  const next = nextPath || window.location.pathname;
  try {
    const res = await fetch('/api/hermes/system/health', { credentials: 'same-origin' });
    if (res.ok) return true;
  } catch (e) { /* network error -> treat as unauthenticated */ }
  window.location.href = '/hermes/auth/?next=' + encodeURIComponent(next);
  return false;
}

// Store pin in localStorage for legacy X-Hermes-PIN header (still sent as backup).
function hermesSetPin(pin) {
  localStorage.setItem('hermes_pin', pin || '');
}
