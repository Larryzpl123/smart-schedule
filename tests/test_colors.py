"""Course colours must come from Shady Side's own department colours."""
import os
import sys, glob, collections, itertools

# Where the app lives. Defaults to the folder next to this one, so the tests
# run from a checkout on any machine; override with SSA_APP if it is elsewhere.
APP_DIR = os.environ.get("SSA_APP") or os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

sys.path.insert(0,APP_DIR)
import pdf_import as PI

# The Veracross PDFs these check against are real student schedules and
# are not in this repository. Without them there is nothing to parse, so
# skip rather than report a failure that is not one.
import glob as _g, os as _o, sys as _s
_dir = _o.environ.get("SSA_PDFS", "/tmp/pdfs")
if not _g.glob(_o.path.join(_dir, "*.pdf")):
    print("  SKIP  no sample PDFs (set SSA_PDFS to a folder of them)")
    _s.exit(0)



FAIL=[]
def ck(c,l):
    print(("  PASS  " if c else "  FAIL  ")+l)
    if not c: FAIL.append(l)

R={f.split('/')[-1]: PI.parse_pdf(f) for f in sorted(glob.glob(os.path.join(os.environ.get("SSA_PDFS", "/tmp/pdfs"), "*.pdf")))}

print("\n[the convention Larry gave]")
WANT={"Computer Science":"orange","Math":"red","Art":"cyan","English":"blue",
      "History":"yellow","Science":"green","World Language":"purple"}
have={s:n for _,n,s in PI.SSA_SUBJECT_FILLS}
for subj,col in WANT.items():
    ck(have.get(subj)==col, f"{subj} -> {col} (table says {have.get(subj)})")

print("\n[palette names must be ones the front-end knows]")
APP_COLORS={"red","green","blue","yellow","purple","teal","orange","pink",
            "gray","grey","indigo","lime","cyan"}
ck(all(n in APP_COLORS for _,n,_ in PI.SSA_SUBJECT_FILLS), "every mapped name is in COLOR_MAP")

print("\n[colours are far enough apart to tell apart]")
d=sorted((sum((a-b)**2 for a,b in zip(x[0],y[0]))**0.5, x[2], y[2])
         for x,y in itertools.combinations(PI.SSA_SUBJECT_FILLS,2))
ck(PI.FILL_TOLERANCE < d[0][0]/2,
   f"tolerance {PI.FILL_TOLERANCE} < half the closest pair {d[0][1]}/{d[0][2]} = {d[0][0]/2:.4f}")

print("\n[every course in every sample got a real department colour]")
for name,r in R.items():
    miss=[c["num"] for c in r["courses"] if not c.get("subject")]
    ck(not miss, f"{name[:26]}: {miss or 'all recognised'}")

print("\n[the same subject is the same colour everywhere]")
byprefix=collections.defaultdict(set)
for r in R.values():
    for c in r["courses"]: byprefix[c["num"][:2]].add((c["color"], c["subject"]))
for k,v in sorted(byprefix.items()):
    ck(len(v)==1, f"{k} -> {sorted(v)}")
ck(byprefix["MA"]=={("red","Math")} and byprefix["CS"]=={("orange","Computer Science")}
   and byprefix["AR"]=={("cyan","Art")} and byprefix["EN"]=={("blue","English")}
   and byprefix["HI"]=={("yellow","History")} and byprefix["FR"]=={("purple","World Language")},
   "the six Larry named land exactly where he said")
ck(byprefix["BI"]==byprefix["CH"]==byprefix["PY"]=={("green","Science")},
   "Biology, Chemistry and Physics all read as Science")
ck(byprefix["EC"]=={("yellow","History")}, "Economics reads as History (not in Larry's list)")
ck(byprefix["PS"]=={("green","Science")}, "Psychology reads as Science (not in Larry's list)")
ck(byprefix["MU"]=={("teal","Music")}, "Chamber Choir has its own colour, not forced into Science")

print("\n[a course that keeps two periods keeps one colour]")
for name,r in R.items():
    bynum=collections.defaultdict(set)
    for c in r["courses"]: bynum[c["num"]].add(c["color"])
    ck(all(len(v)==1 for v in bynum.values()), f"{name[:26]}: one colour per course code")

print("\n[an unknown colour is rejected, not forced into a neighbour]")
n,s = PI.subject_from_fill((0.60,0.60,0.95))         # nothing like any of them
ck(n is None and s is None, f"a far-off fill -> fallback ({n}/{s})")
mid = tuple((a+b)/2 for a,b in zip(
    [f for f,_,s in PI.SSA_SUBJECT_FILLS if s=="Art"][0],
    [f for f,_,s in PI.SSA_SUBJECT_FILLS if s=="English"][0]))
ck(PI.subject_from_fill(mid)[0] is None,
   f"a colour halfway between Art and English is rejected, not guessed ({PI.subject_from_fill(mid)})")
ck(PI.subject_from_fill(None)==(None,None), "no fill at all -> fallback")
ck(PI.subject_from_fill((0.9765,0.8549,0.8549))==("red","Math"), "an exact match still works")

print("\n[greyscale / CMYK fills don't crash]")
ck(PI._as_rgb(0.5)==(0.5,0.5,0.5), "scalar grey")
ck(PI._as_rgb((0,0,0,0))==(1.0,1.0,1.0), "CMYK white")
ck(PI._as_rgb(None) is None, "None")

print("\n"+"="*60); print("ALL PASSED" if not FAIL else f"{len(FAIL)} FAILED: "+"; ".join(FAIL)); print("="*60)
sys.exit(1 if FAIL else 0)
