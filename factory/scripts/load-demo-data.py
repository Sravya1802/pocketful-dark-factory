# Loads synthetic demo data (5 fictional users, payments, requests, a split) through the
# service's own /_test/reset and public API. Usage: python3 factory/scripts/load-demo-data.py [base-url]

import json, urllib.request, uuid
import sys
B=sys.argv[1] if len(sys.argv)>1 else "http://localhost:8080"
def call(m,p,b=None,t=None):
    h={"Content-Type":"application/json"}
    if t: h["Authorization"]="Bearer "+t
    if m=="POST" and not p.startswith(("/auth/","/_test")): h["Idempotency-Key"]=uuid.uuid4().hex
    r=urllib.request.Request(B+p,data=None if b is None else json.dumps(b).encode(),headers=h,method=m)
    try:
        with urllib.request.urlopen(r) as x: return x.status, json.loads(x.read() or b"null")
    except urllib.error.HTTPError as e: return e.code, e.read().decode()[:150]
people=[("ada","Ada Lovelace",48000),("bob","Bob Martin",23500),("cleo","Cleo Park",31000),("dev","Dev Patel",17250),("emma","Emma Rossi",26800)]
call("POST","/_test/reset",{"currency":"EUR","minor_units":2,"users":[{"id":"u_"+h,"email":h+"@example.test","password":"demo-pass-123","display_name":n,"handle":h,"balance":b} for h,n,b in people]})
tok={h: call("POST","/auth/login",{"email":h+"@example.test","password":"demo-pass-123"})[1]["token"] for h,_,_ in people}
ok=0
for f,t,a,n,v in [("bob","ada",1850,"Ramen at Ichiran 🍜","public"),("cleo","ada",4200,"Concert tickets","public"),("ada","dev",900,"Coffee run","public"),("emma","cleo",6500,"Groceries","private"),("dev","bob",1200,"Cab home","public"),("ada","emma",3500,"Birthday gift 🎁","public"),("cleo","dev",2750,"Climbing day pass","public"),("bob","emma",1500,"Book club snacks","public")]:
    ok += call("POST","/payments",{"to_handle":t,"amount":a,"note":n,"visibility":v},tok[f])[0]==201
for f,p,a,n in [("ada","bob",2400,"Your half of the Airbnb deposit"),("ada","cleo",1600,"Pizza night"),("dev","ada",800,"Movie tickets")]:
    ok += call("POST","/requests",{"payer_handle":p,"amount":a,"note":n},tok[f])[0]==201
ok += call("POST","/splits",{"amount":12000,"participant_handles":["bob","cleo","dev"],"note":"Lake trip cabin 🏕️"},tok["ada"])[0]==201
me=call("GET","/me",None,tok["ada"])[1]
print(f"demo reset: {ok}/12 loaded, Ada {me.get('balance')/100:.2f} EUR, held {me.get('held',0)/100:.2f}. Log out and back in as ada@example.test / demo-pass-123")
