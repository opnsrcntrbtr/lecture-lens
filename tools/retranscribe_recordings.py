"""Re-transcribe System Audio chunks of a capture window with whisper.cpp (as the live
follower does) and write lectures/<id>/resources/recording_whisper_cpp.md.
Usage: retranscribe_recordings.py <lecture-folder> <start_utc> <end_utc>"""
import sys, json, datetime as dt
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lecture_kit as lk, live_class as lc, transcript_clean as tc
folder, a, b = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
LOCAL_TZ = dt.datetime.now().astimezone().tzinfo   # this Mac's time zone
con = lk.db()
rows = con.execute("select timestamp ts, file_path fp from audio_chunks where file_path like '%System Audio%' "
                   "and timestamp>=? and timestamp<? order by timestamp", (a, b)).fetchall()
items = [(dt.datetime.fromisoformat(r['ts'][:19]).replace(tzinfo=dt.timezone.utc), Path(r['fp']).name) for r in rows]
out = folder / "resources" / "recording_whisper_cpp.md"
out.parent.mkdir(parents=True, exist_ok=True)
segs = []
for i in range(0, len(items), 10):
    batch = items[i:i + 10]
    try:
        segs += lc.transcribe(batch, batch[0][0], "", 6)
    except Exception as e:
        print("batch", i, "failed:", e, flush=True)
    print(f"{min(i + 10, len(items))}/{len(items)}", flush=True)
seen, lines = set(), []
for s in segs:
    t = s.get("t") or s.get("time"); txt = (s.get("text") or "").strip()
    if not txt or txt in seen: continue
    seen.add(txt)
    ts = dt.datetime.fromisoformat(str(t)).astimezone(LOCAL_TZ).strftime("%H:%M:%S") if t else "--:--:--"
    lines.append(f"[{ts}] {txt}")
out.write_text(f"# Recording playback, re-transcribed with whisper.cpp (this Mac's clock time of playback)\n\n" + "\n".join(lines) + "\n")
print("wrote", out, len(lines), "lines")
