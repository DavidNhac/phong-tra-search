import json
for line in open("data/03_extracted/venues.jsonl", encoding="utf-8"):
    v = json.loads(line)
    ok = [i for i in v["images"] if i.get("status") == "ok"]
    print(v["source"], v["rank_in_article"], v["name"],
          "| địa chỉ:", len(v["addresses"]), "| giờ:", v["opening_hours"],
          "| giá:", v["price_text"], "| ảnh:", len(ok))