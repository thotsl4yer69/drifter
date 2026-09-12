#!/usr/bin/env python3
"""Field-first passive RTL-SDR survey, hunt, listen and IQ capture."""
from __future__ import annotations

import json, logging, math, os, statistics, subprocess, threading, time
from collections import deque
from pathlib import Path
from typing import Iterable

from config import MQTT_HOST, MQTT_PORT, TOPICS, atomic_write_json, make_mqtt_client
from sdr_arbiter import SDRLease, read_owner

log = logging.getLogger("drifter.rfops")
CMD = "drifter/rf/ops/command"; OPS = "drifter/rf/ops"; FINDINGS = "drifter/rf/findings"
HUNT = "drifter/rf/hunt"; CAPTURE = "drifter/rf/capture"; ZOOM = "drifter/rf/spectrum/zoom"
SUMMARY = "drifter/rf/spectrum/summary"; CLASSIFY = "drifter/rf/classification"
STATE = Path(os.getenv("DRIFTER_STATE_DIR", "/opt/drifter/state")); CAPDIR = STATE / "rf_captures"
FINDINGS_FILE = STATE / "rf_findings.json"; BASELINE_FILE = STATE / "rf_baseline.json"
REGION = (os.getenv("DRIFTER_RF_REGION", "AU") or "AU").upper()


def clamp(v, lo, hi): return max(lo, min(hi, v))
def valid_freq(v):
    try: f = float(v)
    except (TypeError, ValueError): return None
    return f if 24 <= f <= 1766 else None


def band_context(f, region=REGION):
    f = float(f)
    if 87.5 <= f <= 108: return "FM broadcast band"
    if 118 <= f <= 137: return "airband"
    if 156 <= f <= 163: return "marine VHF range"
    if 433.05 <= f <= 434.79: return "433 MHz short-range/ISM-LIPD range"
    if region == "AU" and 476.4 <= f <= 477.5: return "AU UHF-CB range"
    if region == "AU" and 915 <= f <= 928: return "AU 915-928 MHz ISM/LIPD range"
    if 1089 <= f <= 1091: return "1090 MHz ADS-B range"
    if 1574 <= f <= 1577: return "GNSS L1 range"
    if 703 <= f <= 960 or 1710 <= f <= 1766: return "cellular-band energy"
    if 144 <= f <= 148: return "2 m amateur range"
    if 430 <= f <= 450: return "70 cm/UHF shared range"
    return "unlabelled spectrum"


def parse_rtl_power(lines: Iterable[str]):
    out = []
    for line in lines:
        p = line.strip().split(",")
        if len(p) < 7: continue
        try: low, step, vals = float(p[2]), float(p[4]), [float(x) for x in p[6:] if x.strip()]
        except ValueError: continue
        out += [{"freq_hz": low + i * step, "db": db} for i, db in enumerate(vals) if math.isfinite(db)]
    return out


def summary_candidates(summary, margin_db=10.0, max_count=7):
    groups = []
    for b in summary.get("bins", []) if isinstance(summary, dict) else []:
        try: groups.append((float(b["freq_hz"]) / 1e6, float(b.get("level_db_max", b["level_db_mean"]))))
        except (KeyError, TypeError, ValueError): pass
    if not groups: return []
    floor = statistics.median(p for _, p in groups)
    ranked = sorted(({"freq_mhz": f, "peak_db": p, "noise_db": floor, "delta_db": p-floor}
                     for f,p in groups if p-floor >= margin_db), key=lambda x:x["delta_db"], reverse=True)
    out=[]
    for c in ranked:
        if any(abs(c["freq_mhz"]-x["freq_mhz"]) < 5 for x in out): continue
        out.append(c)
        if len(out)>=max_count: break
    return out


def analyse_bins(bins, seed_mhz=None):
    clean=[b for b in bins if isinstance(b.get("freq_hz"),(int,float)) and isinstance(b.get("db"),(int,float)) and math.isfinite(b["db"])]
    if not clean: return None
    floor=statistics.median(float(b["db"]) for b in clean); peak=max(clean,key=lambda b:b["db"])
    delta=float(peak["db"])-floor; threshold=floor+max(6,delta*.45); hot=[b for b in clean if b["db"]>=threshold]
    bw=(max(b["freq_hz"] for b in hot)-min(b["freq_hz"] for b in hot))/1000 if hot else 0
    freq=float(peak["freq_hz"])/1e6
    return {"freq_mhz":round(freq,5),"seed_mhz":seed_mhz,"peak_db":round(float(peak["db"]),1),
            "noise_db":round(floor,1),"delta_db":round(delta,1),"bandwidth_khz":round(bw,1),
            "context":band_context(freq),"confidence":round(clamp((delta-5)/25,0,.99),2)}


class RFOps:
    def __init__(self):
        self.client=make_mqtt_client("drifter-rf-ops"); self.client.on_message=self.on_message
        self.running=False; self.lock=threading.Lock(); self.worker=None; self.stop_event=threading.Event(); self.hunt_stop=threading.Event()
        self.pending_survey=False; self.findings=deque(maxlen=50); self._load()

    def _load(self):
        try:
            for x in json.loads(FINDINGS_FILE.read_text()).get("findings",[]):
                if isinstance(x,dict): self.findings.append(x)
        except Exception: pass
    def pub(self,t,p,retain=False): self.client.publish(t,json.dumps(p),qos=1 if retain else 0,retain=retain)
    def ops(self,mode,stage,progress=0,**x): self.pub(OPS,{"mode":mode,"stage":stage,"progress":int(clamp(progress,0,100)),"owner":read_owner().get("owner","idle"),"region":REGION,"ts":time.time(),**x},True)
    def publish_findings(self):
        p={"findings":list(self.findings)[:12],"count":len(self.findings),"region":REGION,"ts":time.time()}
        try: atomic_write_json(FINDINGS_FILE,p)
        except Exception: pass
        self.pub(FINDINGS,p,True)
    def upsert(self,f):
        freq=f.get("freq_mhz"); f.setdefault("first_seen",time.time()); f["last_seen"]=time.time(); f.setdefault("status","NEW")
        for old in list(self.findings):
            try:
                if freq is not None and abs(float(old.get("freq_mhz"))-float(freq)) <= .04:
                    f["first_seen"]=old.get("first_seen",f["first_seen"]); self.findings.remove(old); break
            except Exception: pass
        self.findings.appendleft(f); self.publish_findings()
    def pause(self): self.pub(TOPICS.get("rf_command","drifter/rf/command"),{"command":"pause_rtl_433","reason":"rf_ops","ts":time.time()}); time.sleep(.9)
    def resume(self): self.pub(TOPICS.get("rf_command","drifter/rf/command"),{"command":"resume_rtl_433","reason":"rf_ops","ts":time.time()})
    def start_worker(self,fn,*args,mode):
        with self.lock:
            if self.worker and self.worker.is_alive(): self.ops(mode,"busy",0,error="another RF operation is active"); return False
            self.stop_event.clear(); self.worker=threading.Thread(target=fn,args=args,daemon=True,name=f"rfops-{mode}"); self.worker.start(); return True

    @staticmethod
    def sweep(center,span,bin_hz,timeout=15):
        lo=max(24,center-span/2); hi=min(1766,center+span/2)
        cmd=["rtl_power","-f",f"{lo:.6f}M:{hi:.6f}M:{max(1000,int(bin_hz))}","-i","1","-1"]
        try: r=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout,check=False)
        except Exception: return []
        return parse_rtl_power(r.stdout.splitlines()) if r.returncode==0 else []

    def survey(self):
        with self.lock:
            if self.worker and self.worker.is_alive(): self.ops("survey","busy",0,error="another RF operation is active"); return
            self.pending_survey=True
        self.pub(TOPICS.get("rfaudio_command","drifter/rfaudio/command"),{"action":"stop","ts":time.time()}); time.sleep(.25)
        self.ops("survey","broad sweep",5,message="map spectrum, then zoom strongest candidates")
        self.pub(TOPICS.get("rf_command","drifter/rf/command"),{"command":"force_spectrum","requested_by":"rf_ops","ts":time.time()})
    def deep_survey(self,summary):
        seeds=summary_candidates(summary)
        if not seeds: self.pending_survey=False; self.ops("idle","survey complete",100,message="no peaks cleared adaptive threshold"); return
        self.pause(); lease=SDRLease("rf-survey",detail=f"{len(seeds)} targeted zooms",timeout=8)
        if not lease.acquire(): self.pending_survey=False; self.ops("idle","survey blocked",0,error=f"RTL-SDR busy: {read_owner().get('owner','unknown')}"); self.resume(); return
        try:
            baseline=self._baseline(); total=len(seeds)
            for i,s in enumerate(seeds):
                if self.stop_event.is_set(): break
                self.ops("survey",f"zoom {i+1}/{total}",25+int(70*i/max(1,total)),freq_mhz=s["freq_mhz"])
                f=analyse_bins(self.sweep(s["freq_mhz"],4.0,25000),s["freq_mhz"])
                if not f or f["delta_db"]<6: continue
                f.update({"id":f"rf-{int(time.time())}-{round(f['freq_mhz']*1000)}","status":"KNOWN" if self._known(f["freq_mhz"],baseline) else "NEW","source":"adaptive-survey"}); self.upsert(f)
        finally: lease.release(); self.resume(); self.pending_survey=False
        self.ops("idle","survey complete",100,message=f"{len(self.findings)} findings retained")
    def _baseline(self):
        try: return json.loads(BASELINE_FILE.read_text())
        except Exception: return {}
    @staticmethod
    def _known(freq,baseline):
        for r in baseline.get("findings",[]) if isinstance(baseline,dict) else []:
            try:
                if abs(float(r.get("freq_mhz"))-freq)<=.08:return True
            except Exception: pass
        return False

    def hunt(self,freq): self.hunt_stop.clear(); self.start_worker(self.hunt_worker,freq,mode="hunt")
    def hunt_worker(self,freq):
        self.pause(); lease=SDRLease("rf-hunt",detail=f"{freq:.5f} MHz",timeout=8)
        if not lease.acquire(): self.ops("idle","hunt blocked",0,error=f"RTL-SDR busy: {read_owner().get('owner','unknown')}"); self.resume(); return
        levels=deque(maxlen=8)
        try:
            while not self.hunt_stop.is_set() and not self.stop_event.is_set():
                f=analyse_bins(self.sweep(freq,.24,5000,8),freq)
                if f:
                    levels.append(f["peak_db"]); trend=0 if len(levels)<2 else levels[-1]-levels[0]
                    self.pub(HUNT,{"active":True,**f,"trend_db":round(trend,1),"trend":"stronger" if trend>3 else "weaker" if trend<-3 else "steady","samples":list(levels),"ts":time.time()},True)
                self.ops("hunt","tracking",50,freq_mhz=freq)
                if self.hunt_stop.wait(.2): break
        finally: lease.release(); self.resume(); self.pub(HUNT,{"active":False,"freq_mhz":freq,"ts":time.time()},True); self.ops("idle","hunt stopped",100)

    def capture(self,freq,duration=15): self.start_worker(self.capture_worker,freq,clamp(float(duration),1,30),mode="capture")
    def capture_worker(self,freq,duration):
        self.pause(); lease=SDRLease("rf-capture",detail=f"{freq:.5f} MHz/{duration:.0f}s",timeout=8)
        if not lease.acquire(): self.ops("idle","capture blocked",0,error=f"RTL-SDR busy: {read_owner().get('owner','unknown')}"); self.resume(); return
        CAPDIR.mkdir(parents=True,exist_ok=True); rate=250000; stamp=time.strftime("%Y%m%dT%H%M%S",time.gmtime()); stem=f"{stamp}_{freq:.5f}MHz"
        data=CAPDIR/f"{stem}.sigmf-data"; meta=CAPDIR/f"{stem}.sigmf-meta"; self.ops("capture","recording IQ",20,freq_mhz=freq,duration_s=duration)
        try:
            r=subprocess.run(["rtl_sdr","-f",str(int(freq*1e6)),"-s",str(rate),"-n",str(int(rate*duration)),str(data)],capture_output=True,timeout=duration+8,check=False)
            if r.returncode or not data.exists(): raise RuntimeError((r.stderr or b"rtl_sdr failed").decode(errors="replace")[:180])
            atomic_write_json(meta,{"global":{"core:datatype":"cu8","core:sample_rate":rate,"core:version":"1.0.0","core:description":"DRIFTER passive RTL-SDR field capture","drifter:region":REGION},"captures":[{"core:sample_start":0,"core:frequency":int(freq*1e6),"core:datetime":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}],"annotations":[]})
            p={"ok":True,"freq_mhz":round(freq,5),"duration_s":duration,"sample_rate":rate,"data_file":data.name,"meta_file":meta.name,"bytes":data.stat().st_size,"ts":time.time()}; self.pub(CAPTURE,p,True); self.ops("idle","capture saved",100,**p)
        except Exception as e:
            try:data.unlink(missing_ok=True)
            except OSError:pass
            self.pub(CAPTURE,{"ok":False,"error":str(e),"ts":time.time()},True); self.ops("idle","capture failed",0,error=str(e))
        finally: lease.release(); self.resume()

    def zoom(self,freq,span_khz=500): self.start_worker(self.zoom_worker,freq,int(clamp(span_khz,50,5000)),mode="zoom")
    def zoom_worker(self,freq,span):
        self.pause(); lease=SDRLease("rf-zoom",detail=f"{freq:.5f} MHz",timeout=8)
        if not lease.acquire(): self.ops("idle","zoom blocked",0,error="RTL-SDR busy"); self.resume(); return
        try:
            bins=self.sweep(freq,span/1000,max(2500,span*1000//120),12); self.pub(ZOOM,{"center_mhz":freq,"span_khz":span,"bins":[{"freq_hz":round(b["freq_hz"]),"db":round(b["db"],1)} for b in bins[:400]],"analysis":analyse_bins(bins,freq),"ts":time.time()},True); self.ops("idle","zoom complete",100,freq_mhz=freq)
        finally: lease.release(); self.resume()

    def listen(self,freq,mode="nfm"):
        with self.lock: busy=bool(self.worker and self.worker.is_alive()); hunting=read_owner().get("owner")=="rf-hunt"
        if busy and not hunting: self.ops("listen","busy",0,error="finish current RF operation first"); return
        if hunting:
            self.hunt_stop.set(); deadline=time.monotonic()+3
            while read_owner().get("owner")=="rf-hunt" and time.monotonic()<deadline: time.sleep(.08)
        self.pub(TOPICS.get("rfaudio_command","drifter/rfaudio/command"),{"action":"start","freq_mhz":freq,"mode":mode,"gain":0,"ts":time.time()}); self.ops("listen","starting audio",20,freq_mhz=freq,demod=mode)
    def recover(self): self.hunt_stop.set(); self.stop_event.set(); self.pub(TOPICS.get("rfaudio_command","drifter/rfaudio/command"),{"action":"stop","ts":time.time()}); self.resume(); self.ops("idle","RF workers reset",100)

    def command(self,d):
        a=str(d.get("action") or "").lower(); f=valid_freq(d.get("freq_mhz")) if a in {"hunt_start","capture","zoom","listen"} else None
        if a=="survey": self.survey()
        elif a=="survey_stop": self.stop_event.set()
        elif a=="hunt_stop": self.hunt_stop.set(); self.stop_event.set()
        elif a in {"hunt_start","capture","zoom","listen"} and f is None: self.ops("idle","invalid request",0,error="freq_mhz must be 24-1766")
        elif a=="hunt_start": self.hunt(f)
        elif a=="capture": self.capture(f,d.get("duration_s",15))
        elif a=="zoom": self.zoom(f,int(d.get("span_khz",500)))
        elif a=="listen": self.listen(f,str(d.get("mode") or "nfm").lower())
        elif a=="listen_stop": self.pub(TOPICS.get("rfaudio_command","drifter/rfaudio/command"),{"action":"stop","ts":time.time()}); self.ops("idle","audio stopped",100)
        elif a=="baseline_save": atomic_write_json(BASELINE_FILE,{"region":REGION,"findings":list(self.findings)[:12],"ts":time.time()}); self.ops("idle","baseline saved",100)
        elif a=="recover": self.recover()
        else:self.ops("idle","invalid request",0,error=f"unknown RF action: {a or '(empty)'}")

    def on_message(self,client,userdata,msg):
        try:d=json.loads(msg.payload)
        except Exception:return
        if msg.topic==CMD and isinstance(d,dict): self.command(d); return
        if msg.topic==SUMMARY and isinstance(d,dict) and self.pending_survey and d.get("forced"): self.start_worker(self.deep_survey,d,mode="survey"); return
        if msg.topic==TOPICS.get("rf_signal","drifter/rf/signals") and isinstance(d,dict):
            raw=d.get("raw") if isinstance(d.get("raw"),dict) else {}; f=valid_freq(raw.get("freq") or raw.get("freq_mhz") or 433.92); model=str(d.get("model") or "unknown")
            if f and model.lower() not in {"","unknown"}: self.upsert({"id":f"decode-{d.get('id') or model}-{round(f*1000)}","freq_mhz":round(f,5),"context":band_context(f),"classification":model,"protocol":d.get("protocol") or "","confidence":.9,"status":"KNOWN","source":"rtl_433"})
        elif msg.topic==CLASSIFY and isinstance(d,dict):
            f=valid_freq(d.get("freq_mhz") or (float(d.get("frequency_hz"))/1e6 if d.get("frequency_hz") else None))
            if f:self.upsert({"id":f"classify-{int(time.time())}-{round(f*1000)}","freq_mhz":round(f,5),"context":band_context(f),"classification":d.get("protocol") or d.get("modulation") or "unknown waveform","confidence":d.get("confidence",.5),"status":"NEW" if d.get("novel") else "UNKNOWN","source":"classifier"})

    def start(self):
        if self.running:return
        self.running=True; STATE.mkdir(parents=True,exist_ok=True)
        try:
            self.client.connect(MQTT_HOST,MQTT_PORT,30); self.client.subscribe([(CMD,1),(SUMMARY,0),(TOPICS.get("rf_signal","drifter/rf/signals"),0),(CLASSIFY,0)]); self.client.loop_start(); self.publish_findings(); self.ops("idle","ready",100); log.info("RF operator engine ready")
        except Exception as e:self.running=False; log.warning("RF operator engine start failed: %s",e)
    def stop(self):
        if not self.running:return
        self.stop_event.set();self.hunt_stop.set();self.pub(TOPICS.get("rfaudio_command","drifter/rfaudio/command"),{"action":"stop","ts":time.time()})
        try:self.client.loop_stop();self.client.disconnect()
        except Exception:pass
        self.running=False

_ENGINE=None
def start():
    global _ENGINE
    if _ENGINE is None:_ENGINE=RFOps()
    _ENGINE.start();return _ENGINE
def stop():
    if _ENGINE is not None:_ENGINE.stop()
