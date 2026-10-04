#!/usr/bin/env python3
"""UI mutation sanity: break the analyst's reference UI one way at a time and confirm the UI checks fail.
Run with the Playwright python:  .../.venv/bin/python selftest/mutate_ui.py   (from evidence/stage-2)"""
import os, subprocess, sys, tempfile, time, shutil
HERE = os.path.dirname(os.path.abspath(__file__))
UI = open(os.path.join(HERE, "refui.html")).read()
M = {
 "float-money-parse": ("function parseDec(str,mu){", "function parseDec(str,mu){const n=Number(str);if(!isFinite(n)||n<0||str.trim()==='')return null;return Math.floor(n*Math.pow(10,mu));"),
 "refresh-last-response-wins": ("if(my<applied)return;", ""),
 "retry-gets-a-new-key": ("if(!st.pending||st.pending.fp!==fp)st.pending={fp,key:uid()};", "st.pending={fp,key:uid()};"),
 "unchanged-resubmit-sends-again": ("if(st.lastOkFp===fp)return; // unchanged form after success: no new request", ""),
 "held-shown-at-zero": ("ME.held>0?", "true?"),
 "balance-shows-available": ("tid('wallet-balance','span',{'data-amount':ME.total,text:money(ME.total)})", "tid('wallet-balance','span',{'data-amount':ME.available,text:money(ME.available)})"),
 "uncertain-shown-as-error": ("msg.append(tid('pay-uncertain','div',{class:'unc',role:'status','data-state':'uncertain',", "msg.append(tid('pay-error','div',{class:'err',role:'alert','data-state':'uncertain',"),
 "form-cleared-after-refusal": ("else{st.pending=null;msg.append(tid(o.p+'-error'", "else{document.querySelectorAll('[data-testid^=\"'+o.p+'-\"]').forEach(e=>{if(e.tagName==='INPUT')e.value=''});st.pending=null;msg.append(tid(o.p+'-error'"),
 "stale-pay-button-stays": ("if(r.status>=400)err.append(tid('request-error','div',{class:'err',role:'alert',text:errText(r)}));await load()}}));\n   if(q.status==='pending'&&mine)", "if(r.status>=400)err.append(tid('request-error','div',{class:'err',role:'alert',text:errText(r)}));}}));\n   if(q.status==='pending'&&mine)"),
 "split-preview-remainder-last": ("return Array.from({length:n},(_,i)=>q+(i<r?1:0))", "return Array.from({length:n},(_,i)=>q+(i>=n-r?1:0))"),
 "small-text-low-contrast": (".sec{font-size:.95rem;color:var(--muted)}", ".sec{font-size:.95rem;color:#b5bcc4}"),
 "no-focus-indicator": (":focus-visible{outline:3px solid #ffb703;outline-offset:2px}", ":focus-visible{outline:none}"),
 "wide-note-overflow": ("overflow-wrap:anywhere;word-break:break-word}", "white-space:nowrap}"),
 "external-font": ("<title>", "<link rel=\"stylesheet\" href=\"https://fonts.googleapis.com/css2?family=Inter\"><title>"),
}
IDS = {"float-money-parse": "UI-20,UI-21", "refresh-last-response-wins": "UI-52", "retry-gets-a-new-key": "UI-40,UI-42", "unchanged-resubmit-sends-again": "UI-30",
       "held-shown-at-zero": "UI-10", "balance-shows-available": "UI-13", "uncertain-shown-as-error": "UI-40", "form-cleared-after-refusal": "UI-32",
       "stale-pay-button-stays": "UI-73", "split-preview-remainder-last": "UI-80", "small-text-low-contrast": "UI-127", "no-focus-indicator": "UI-125",
       "wide-note-overflow": "UI-120", "external-font": "UI-130"}
bad = 0
tmp = tempfile.mkdtemp()
shutil.copy(os.path.join(HERE, "refimpl2.py"), tmp)
for name, (a, b) in M.items():
    if a not in UI:
        print("!! %s: anchor not found" % name); bad += 1; continue
    open(os.path.join(tmp, "refui.html"), "w").write(UI.replace(a, b, 1))
    srv = subprocess.Popen([sys.executable, os.path.join(tmp, "refimpl2.py")], env={**os.environ, "PORT": "8096"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    out = subprocess.run([sys.executable, os.path.join(HERE, "..", "run_ui_checks.py"), "http://127.0.0.1:8096", "-q", "--ids", IDS[name]], capture_output=True, text=True).stdout
    srv.kill()
    last = [l for l in out.splitlines() if l.startswith("UI checks run")][-1]
    killed = "failed: 0  errors: 0" not in last
    print("%-34s %s  [%s]  %s" % (name, "KILLED" if killed else "SURVIVED", IDS[name], last)); sys.stdout.flush()
    bad += (not killed)
shutil.rmtree(tmp)
sys.exit(1 if bad else 0)
