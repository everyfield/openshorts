import React, { useCallback, useEffect, useState } from 'react';
import { Instagram, Loader2, Link2, Unlink } from 'lucide-react';
import { apiJson } from '../lib/api';

export default function InstagramConnectionsCard() {
  const [connections, setConnections] = useState([]);
  const [clips, setClips] = useState([]);
  const [selectedConnection, setSelectedConnection] = useState('');
  const [selectedClip, setSelectedClip] = useState('');
  const [caption, setCaption] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');

  const load = useCallback(async () => {
    try {
      const [data, library] = await Promise.all([
        apiJson('/api/native-social/instagram/connections'),
        apiJson('/api/native-social/instagram/clips'),
      ]);
      const accounts = Array.isArray(data.connections) ? data.connections : [];
      const availableClips = Array.isArray(library.clips) ? library.clips : [];
      setConnections(accounts);
      setClips(availableClips);
      setSelectedConnection((current) => current || accounts.find((x) => x.health === 'connected')?.id || '');
      setSelectedClip((current) => current || availableClips[0]?.id || '');
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

  const publish = async () => {
    if (!selectedConnection || !selectedClip) return;
    setBusy(true);
    setMessage('Submitting Reel to Instagram…');
    try {
      const result = await apiJson('/api/native-social/instagram/publish', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          connection_id: selectedConnection,
          clip_id: selectedClip,
          caption,
          idempotency_key: window.crypto.randomUUID(),
        }),
      });
      setMessage(result.status === 'published'
        ? 'Reel published successfully.'
        : `Publish status: ${result.status}. Job ID: ${result.job_id}`);
    } catch (error) {
      setMessage(error?.detail || 'Could not publish this Reel.');
    } finally {
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
        Connect a professional Instagram Business or Creator account. Select an archived clip and publish it as an Instagram Reel. Existing Upload-Post publishing remains separate.
      </p>
      {message && <p role="status" className="text-xs text-ink2 mb-3">{message}</p>}
      {loading ? (
        <div className="flex items-center gap-2 text-xs text-muted"><Loader2 size={14} className="animate-spin" /> Loading accounts…</div>
      ) : connections.length === 0 ? (
        <p className="text-xs text-muted">No native Instagram accounts connected.</p>
      ) : (
        <div className="space-y-3">
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
          <div className="border-t border-rule pt-4 space-y-3">
            <h3 className="text-sm font-medium text-ink">Publish a generated clip</h3>
            <label className="block text-xs text-muted">
              Instagram account
              <select value={selectedConnection} onChange={(e) => setSelectedConnection(e.target.value)}
                className="mt-1 w-full rounded-input border border-rule bg-paper2 px-3 py-2 text-sm text-ink">
                {connections.filter((a) => a.health === 'connected').map((account) => (
                  <option key={account.id} value={account.id}>@{account.username}</option>
                ))}
              </select>
            </label>
            <label className="block text-xs text-muted">
              Generated clip
              <select value={selectedClip} onChange={(e) => setSelectedClip(e.target.value)}
                className="mt-1 w-full rounded-input border border-rule bg-paper2 px-3 py-2 text-sm text-ink">
                <option value="">Select a clip</option>
                {clips.map((clip) => (
                  <option key={clip.id} value={clip.id}>{clip.title} · {new Date(clip.created_at).toLocaleDateString()}</option>
                ))}
              </select>
            </label>
            <label className="block text-xs text-muted">
              Caption
              <textarea value={caption} onChange={(e) => setCaption(e.target.value)} maxLength={2200}
                rows={3} placeholder="Write your Reel caption…"
                className="mt-1 w-full rounded-input border border-rule bg-paper2 px-3 py-2 text-sm text-ink" />
            </label>
            <button onClick={publish} disabled={busy || !selectedConnection || !selectedClip}
              className="btn-primary px-3 py-2 text-xs disabled:opacity-50">
              {busy ? <Loader2 size={14} className="animate-spin" /> : <Instagram size={14} />}
              Publish Reel
            </button>
            {clips.length === 0 && <p className="text-xs text-muted">No archived clips are available. Generate a clip first; it will appear here after it is saved to your library.</p>}
          </div>
        </div>
      )}
    </section>
  );
}
