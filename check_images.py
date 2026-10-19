import json
for line in open("data/03_extracted/venues.jsonl", encoding="utf-8"):
    v = json.loads(line)
    if v["source"] != "luhanhvietnam":
        continue
    print(v["rank_in_article"], v["name"])
    for i in v["images"]:
        print("    ", i.get("status"), i.get("width"), "x", i.get("height"),
              "|", i.get("alt") or "-", "|", i.get("caption") or "-", "|", i["src"].split("/")[-1])