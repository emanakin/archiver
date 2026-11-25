"use client";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Disc3, Plus, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import type { components } from "@/lib/api.generated";
import { useArchive } from "../lib/archive-context";
import { Badge, Button, Card, Field, Input, Modal, Select } from "./ui";

type Library = components["schemas"]["MusicLibrary"];
type Entry = components["schemas"]["MusicPlaylist"];

function playlistId(input: string) {
  const match = /^(?:https:\/\/open\.spotify\.com\/(?:intl-[a-z]+\/)?playlist\/|spotify:playlist:)?([A-Za-z0-9]{22})\/?(?:\?[^#\s]*)?$/.exec(input.trim());
  return match?.[1];
}

export function MusicLibrary() {
  const { data, notify } = useArchive();
  const client = useQueryClient();
  const base = `/accounts/${data.account.id}`;
  const key = ["music-library", data.account.id];
  const library = useQuery({ queryKey: key, queryFn: () => api<Library>(base + "/music-library"), refetchInterval: 15000 });
  const sounds = useQuery({queryKey:["saved-audio",data.account.id],queryFn:()=>api<components["schemas"]["SavedAudioResponse"][]>(base + "/saved-audio")});
  const [selected, select] = useState("");
  const [soundId, selectSound] = useState("");
  const [songId, selectSong] = useState("");
  const history = useQuery({queryKey:["spotify-playlist",data.account.id],queryFn:()=>api<components["schemas"]["PlaylistResponse"] | null>(base+"/spotify-playlist")});
  const recent = history.data?.recent ?? [];
  const song = recent.find(s=>s.spotify_id===songId) ?? recent[0];
  const [dialog, setDialog] = useState<"add"|"edit"|null>(null);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const entries = library.data?.playlists ?? [];
  const active = entries.find(p=>p.playlist_id === selected) ?? entries[0];
  const sound = sounds.data?.find(s=>s.audio_id === soundId) ?? sounds.data?.[0];
  const preferences = entries.map(({playlist_id,label,enabled})=>({playlist_id,label,enabled}));
  const run = async (action:()=>Promise<void>) => {
    setBusy(true); setError("");
    try { await action(); }
    catch(e) { setError(e instanceof Error ? e.message : "Could not save the playlist."); }
    finally { setBusy(false); }
  };
  const save = async (playlists: Entry[]) => {
    const updated = await api<Library>(base+"/music-library",{method:"PUT",body:JSON.stringify({expected_version:library.data!.version,playlists})});
    client.setQueryData(key,updated);
    await Promise.all(["workspace","spotify-playlist"].map(k=>client.invalidateQueries({queryKey:[k]})));
    setDialog(null);
  };
  return <div className="library-grid"><Card className="settings-card"><div className="settings-card-heading">
    <span className="settings-section-icon"><Disc3 size={20}/></span><div><h2>Playlist rotation</h2><p>Active playlists share one pool of unused tracks.</p></div>
    </div><div className="settings-card-body connection-form">
      {library.isPending ? <p>Loading playlists…</p> : library.isError ? <Button onClick={()=>void library.refetch()}>Retry playlists</Button> : <>
        {active && <><Field label="Playlist" htmlFor="library-playlist"><Select id="library-playlist" label="Playlist" value={active.playlist_id} onValueChange={select} options={entries.map(p=>({value:p.playlist_id,label:p.label}))}/></Field>
        <div className="flex flex-wrap items-center gap-2"><Badge tone={active.enabled?"green":"neutral"}>{active.enabled?"In rotation":"Paused"}</Badge><span>{active.unused} unused tracks · Sync {active.sync_state}</span></div>
        <div className="flex flex-wrap gap-2"><Button variant="outline" disabled={busy} onClick={()=>void run(()=>save(preferences.map(p=>p.playlist_id===active.playlist_id?{...p,enabled:!p.enabled}:p)))}>{active.enabled?"Pause playlist":"Resume playlist"}</Button>
        <Button variant="ghost" disabled={busy} onClick={()=>{setName(active.label);setUrl(active.playlist_id);setError("");setDialog("edit");}}>Edit playlist</Button>
        <Button variant="ghost" disabled={busy || !active.enabled} onClick={()=>void run(async()=>{await api(base+`/spotify-playlist/refresh?playlist_id=${active.playlist_id}`,{method:"POST"});await library.refetch();notify("Playlist refreshed.");})}><RefreshCw size={15}/> Sync now</Button></div></>}
        <Button variant="outline" disabled={busy} onClick={()=>{setUrl("");setName("");setError("");setDialog("add");}}><Plus size={16}/> Add playlist</Button>
        {song && <details><summary>Recently selected songs</summary><div className="connection-form"><Select label="Selected song" value={song.spotify_id} onValueChange={selectSong} options={recent.map(s=>({value:s.spotify_id,label:`${s.title} · ${s.artist}`}))}/><a className="text-link" href={`https://open.spotify.com/track/${song.spotify_id}`} target="_blank" rel="noreferrer">Open song on Spotify</a></div></details>}
      </>}
      {error && !dialog && <p role="alert">{error}</p>}
    </div></Card>
    <Card className="settings-card"><div className="settings-card-heading"><div><h2>Saved Instagram sounds</h2><p>Audio shared with the archive inbox.</p></div></div><div className="settings-card-body connection-form">
      {sound ? <><Select label="Saved sound" value={sound.audio_id} onValueChange={selectSound} options={sounds.data!.map(s=>({value:s.audio_id,label:[s.title,s.artist].filter(Boolean).join(" · ")}))}/>
      <p>{sound.state==="feed_ready" ? sound.used_at?"Used in a draft":"Ready for a feed post" : sound.state.replaceAll("_"," ")}</p><a className="text-link" href={`https://www.instagram.com/reels/audio/${sound.audio_id}/`} target="_blank" rel="noreferrer">Open sound on Instagram</a></> : <p>{sounds.isPending?"Loading sounds…":sounds.isError?"Saved sounds unavailable.":"No sounds saved yet."}</p>}
    </div></Card>
    <Modal open={!!dialog} onOpenChange={open=>{if(!open)setDialog(null);}} title={dialog==="edit"?"Edit playlist":"Add a Spotify playlist"} description="Public playlists sync every few hours. Previously selected songs stay in the archive’s history.">
      <form className="connection-form" onSubmit={e=>{e.preventDefault();void run(async()=>{const id=playlistId(url);
        if(!id || !name.trim())throw new Error("Enter a playlist name and a public Spotify playlist link.");
        if(dialog==="add" && entries.some(p=>p.playlist_id===id))throw new Error("This playlist is already in your library.");
        await save(dialog==="edit"?preferences.map(p=>p.playlist_id===id?{...p,label:name.trim()}:p):[...preferences,{playlist_id:id,label:name.trim(),enabled:true}]); select(id);
      });}}>
        <Field label="Playlist name" htmlFor="playlist-name"><Input id="playlist-name" value={name} required maxLength={80} onChange={e=>setName(e.target.value)}/></Field>
        {dialog==="add" && <Field label="Spotify playlist link" htmlFor="playlist-url"><Input id="playlist-url" value={url} required placeholder="https://open.spotify.com/playlist/…" onChange={e=>setUrl(e.target.value)}/></Field>}
        {error && <p role="alert">{error}</p>}
        <div className="flex gap-2"><Button type="submit" loading={busy}>Save playlist</Button>{dialog==="edit" && <Button type="button" variant="ghost" disabled={busy} onClick={()=>void run(()=>save(preferences.filter(p=>p.playlist_id!==url)))}>Remove from library</Button>}</div>
      </form>
    </Modal></div>;
}
