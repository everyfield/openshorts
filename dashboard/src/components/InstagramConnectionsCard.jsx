import React, { useCallback, useEffect, useState } from 'react';
import { Instagram, Loader2, Link2, Unlink } from 'lucide-react';
import { apiJson } from '../lib/api';

export default function InstagramConnectionsCard() {
  const [connections, setConnections] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');

  const load = useCallback(async () => {
    try {
      const data = await apiJson('/api/native-social/instagram/connections');
      setConnections(Array.isArray(data.connections) ? data.connections : []);
    } catch (_) {
      setConnections([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let status = '';
    try {
      const params = new URLSearchParams(window.location.search);
      status = params.get('instagram_connect') || '';
      if (status) {
        params.delete('instagram_connect');
        const query = params.toString();
        window.history.replaceState(null, '', window.location.pathname + (query ? '?' + query : '') + window.location.hash);
      }
    } catch (_) {}
    if (status === 'success') setMessage('Instagram account connected.');
    else if (status === 'error') setMessage('Instagram connection failed. Check app configuration and try again.');
    load();
  }, [load]);

  const connect = async () => {
    setBusy(true);
    setMessage('');
    try {
      const data = await apiJson('/api/native-social/instagram/connect');
      if (!data.authorization_url) throw new Error('Missing authorization URL');
      window.location.assign(data.authorization_url);
    } catch (error) {
      setMessage(error?.detail || 'Could not start Instagram connection.');
      setBusy(false);
    }
  };

  const disconnect = async (id) => {
    if (!window.confirm('Disconnect this Instagram account? Its saved access token will be deleted.')) return;
    setBusy(true);
    try {
      await apiJson('/api/native-social/instagram/connections/' + encodeURIComponent(id), { method: 'DELETE' });
      await load();
      setMessage('Instagram account disconnected. Revoke app access in Instagram settings separately if needed.');
    } catch (error) {
      setMessage(error?.detail || 'Could not disconnect Instagram account.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card p-6 mb-6">
      <div className="flex items-center justify-between gap-3 mb-2">
        <h2 className="text-base font-medium text-ink lowercase flex items-center gap-2">
          <Instagram size={17} className="text-brass" /> Native Instagram publishing
        </h2>
        <button onClick={connect} disabled={busy} className="btn-primary px-3 py-2 text-xs">
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Link2 size={14} />}
          Connect Instagram
        </button>
      </div>
      <p className="text-xs text-muted leading-relaxed mb-4">
        Connect a professional Instagram Business or Creator account. Native Reels publishing is being completed; existing Upload-Post publishing remains separate.
      </p>
      {message && <p role="status" className="text-xs text-ink2 mb-3">{message}</p>}
      {loading ? (
        <div className="flex items-center gap-2 text-xs text-muted"><Loader2 size={14} className="animate-spin" /> Loading accounts…</div>
      ) : connections.length === 0 ? (
        <p className="text-xs text-muted">No native Instagram accounts connected.</p>
      ) : (
        <div className="space-y-2">
          {connections.map((account) => (
            <div key={account.id} className="flex items-center justify-between gap-3 border-t border-rule pt-3">
              <div className="min-w-0">
                <p className="text-sm text-ink truncate">@{account.username}</p>
                <p className="text-xs text-muted">{account.health === 'connected' ? 'Connected' : account.health}</p>
              </div>
              <button onClick={() => disconnect(account.id)} disabled={busy}
                className="btn-quiet px-3 py-2 text-xs shrink-0">
                <Unlink size={13} /> Disconnect
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
