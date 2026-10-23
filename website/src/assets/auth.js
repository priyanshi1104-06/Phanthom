// Phantom accounts: "Continue with Google" through Amazon Cognito, with the OAuth 2.0 authorization code flow
// and PKCE (no secret in the browser). The page's <meta name="phantom-auth" content="domain|client id"> says where
// Cognito is; without it, sign-in is off and the pages say accounts are coming.
//
// Signing in is joining early access: Cognito keeps the list of everyone who has (AWS console > Cognito > Users).
// The ID token is kept for this tab only (sessionStorage) and is used to show the account page, nothing else.
(() => {
  const meta = document.querySelector('meta[name="phantom-auth"]');
  const [domain, clientId] = (meta && meta.content ? meta.content : '').split('|');
  const configured = Boolean(domain && clientId);
  const KEYS = { verifier: 'phantom-pkce', state: 'phantom-state', token: 'phantom-id-token' };

  const store = {
    get(key) { try { return sessionStorage.getItem(key); } catch { return null; } },
    set(key, value) { try { sessionStorage.setItem(key, value); } catch { /* storage blocked */ } },
    drop(key) { try { sessionStorage.removeItem(key); } catch { /* storage blocked */ } },
  };

  const base64url = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes)))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

  const randomString = () => base64url(crypto.getRandomValues(new Uint8Array(48)));

  async function challengeFor(verifier) {
    return base64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)));
  }

  const callback = () => `${location.origin}/account.html`;

  async function signIn() {
    const verifier = randomString();
    const state = randomString();
    store.set(KEYS.verifier, verifier);
    store.set(KEYS.state, state);
    const query = new URLSearchParams({
      response_type: 'code', client_id: clientId, redirect_uri: callback(), identity_provider: 'Google',
      scope: 'openid email profile', state, code_challenge: await challengeFor(verifier),
      code_challenge_method: 'S256',
    });
    location.assign(`https://${domain}/oauth2/authorize?${query}`);
  }

  /** The signed-in person from the ID token ({email, name}), or null when there is none or it has expired. */
  function profile() {
    const token = store.get(KEYS.token);
    if (!token) {
      return null;
    }
    try {
      const part = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
      const claims = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(part), (c) => c.charCodeAt(0))));
      return claims.exp * 1000 > Date.now() ? { email: claims.email || '', name: claims.name || claims.given_name || '' } : null;
    } catch {
      return null;
    }
  }

  /** On account.html after Google: trade the one-time code for tokens. Returns an error message, or ''. */
  async function finishSignIn() {
    const params = new URLSearchParams(location.search);
    const code = params.get('code');
    if (!code) {
      return params.get('error_description') || params.get('error') || '';
    }
    history.replaceState(null, '', location.pathname);
    const verifier = store.get(KEYS.verifier);
    const state = store.get(KEYS.state);
    store.drop(KEYS.verifier);
    store.drop(KEYS.state);
    if (!verifier || params.get('state') !== state) {
      return 'This sign-in link was already used or came from another tab. Please sign in again.';
    }
    const response = await fetch(`https://${domain}/oauth2/token`, {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'authorization_code', client_id: clientId, code, redirect_uri: callback(), code_verifier: verifier,
      }),
    });
    if (!response.ok) {
      return 'Google sign-in could not be completed. Please try again.';
    }
    const tokens = await response.json();
    store.set(KEYS.token, tokens.id_token);
    return '';
  }

  function signOut() {
    store.drop(KEYS.token);
    const query = new URLSearchParams({ client_id: clientId, logout_uri: `${location.origin}/` });
    location.assign(`https://${domain}/logout?${query}`);
  }

  // ---------- the pages ----------

  if (!configured) {
    return;
  }
  for (const button of document.querySelectorAll('[data-auth="signin"]')) {
    button.removeAttribute('aria-disabled');
    button.addEventListener('click', () => { void signIn(); });
  }
  const person = profile();
  if (person && document.querySelector('[data-auth="signin"]')) {  // already signed in: straight to the account
    location.replace('account.html');
    return;
  }
  for (const link of document.querySelectorAll('[data-auth="header"]')) {
    if (person) {
      link.textContent = 'Account';
      link.setAttribute('href', 'account.html');
    }
  }
  const account = document.getElementById('account');
  if (account) {
    const show = (name) => {
      for (const part of account.querySelectorAll('[data-state]')) {
        part.hidden = part.dataset.state !== name;
      }
    };
    finishSignIn().then((problem) => {
      const who = profile();
      if (who) {
        account.querySelector('[data-field="name"]').textContent = who.name ? `, ${who.name.split(' ')[0]}` : '';
        account.querySelector('[data-field="email"]').textContent = who.email;
        show('in');
      } else if (problem) {
        account.querySelector('[data-field="problem"]').textContent = problem;
        show('error');
      } else {
        location.replace('signin.html');
      }
    }, () => {
      account.querySelector('[data-field="problem"]').textContent = 'Phantom could not reach the sign-in service.';
      show('error');
    });
    const out = account.querySelector('[data-auth="signout"]');
    if (out) {
      out.addEventListener('click', signOut);
    }
  }
})();
