"""Synthetic ReportedYears for smoke-testing without a real FRS PDF."""
from pathlib import Path
from pfa.extraction.frs import ReportedYear

CATS = {"1":1_580_097,"2":0,"3":7_751_280,"4":29_259_742,"6":1_545_403,"7":374_800,
        "8":1_989_786,"9":83_355,"10":0,"11":361_037,"12A":1_394_305,"12B":4_600,
        "12C":164_975,"13":20_000,"13A":1_356_605,"14":0,"15":1_409_921,"16":32_297,
        "17":38_612,"18":841_893,"19":343_738,"20":10_880_062,"21":498_931,
        "22":10_780_239,"24":7_981_277,"26":354_083,"27":1_012_907,"28":3_698_190,
        "29":1_511_448,"30":1_928_721,"31":323_271,"32":3_143,"33":171_860,
        "34":2_073_386,"35":2_980_891,"35A":422_334,"36":1_545_403,"37":586_811,
        "38":141_931,"39":582_126,"40":410_673,"43":4_081_350,"44":0}

SPORTS = {"male":["Baseball","Basketball","Golf","Track and Field, X-Country"],
          "female":["Basketball","Softball","Volleyball","Soccer"]}

def year(fy, scale=1.0, institution="Lakeview State University"):
    r = ReportedYear(path=Path(f"synthetic-FY{fy}.pdf"), institution=institution,
                     fiscal_year=fy, categories={k:int(v*scale) for k,v in CATS.items()},
                     cost_of_attendance_in_state=28_500, cost_of_attendance_out_of_state=41_200)
    for gender, sports in SPORTS.items():
        for i, s in enumerate(sports):
            r.operating[(gender,s)]      = round((120_000 + i*25_000) * scale)
            r.aid_dollars[(gender,s)]    = round((300_000 + i*40_000) * scale)
            r.equivalencies[(gender,s)]  = round((6.0 + i) * scale, 2)
            r.coaching[(gender,s)]       = round((180_000 + i*30_000) * scale)
            r.roster[(gender,s)]         = 18 + i*4
    return r
