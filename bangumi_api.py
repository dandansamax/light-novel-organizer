import json
from difflib import get_close_matches

import requests

headers = {
    "user-agent": "dandansamax/light-novel-manager",
    "accept": "application/json",
}

base_url = "https://api.bgm.tv"


def search_novel(keyword):
    search_url = f"{base_url}/v0/search/subjects"
    payload = {
        "keyword": keyword,
        "sort": "match",
        "filter": {"type": [1], "tag": [], "air_date": [], "rating": []},
    }
    post_headers = headers.copy()
    post_headers["Content-Type"] = "application/json"
    r = requests.post(search_url, data=json.dumps(payload), headers=post_headers)
    result = r.json()
    if "data" not in result or not result["data"]:
        raise RuntimeError(f'Cannot find a novel named "{keyword}".')

    name_id_map = {}
    for subject in result["data"]:
        # Remove manga subjects
        if any(["漫画" in tag["name"] for tag in subject["tags"]]):
            continue

        name = subject["name_cn"] if "name_cn" in subject else subject["name"]
        id = subject["id"]
        name_id_map[name] = id
    match_names = get_close_matches(keyword, name_id_map.keys(), n=3, cutoff=0.5)
    if not match_names:
        raise RuntimeError(f'Cannot find a novel by "{keyword}".')
    return {"id": name_id_map[match_names[0]], "name": match_names[0]}


def check_id(subject_id):
    search_url = f"{base_url}/v0/subjects/{subject_id}"
    r = requests.get(search_url, headers=headers)
    result = r.json()
    if result["id"] == subject_id:
        return None
    else:
        return {
            "id": result["id"],
            "name": result["name_cn"] if "name_cn" in result else result["name"],
        }


def get_person_by_id(subject_id):
    authors = []
    illustrators = []
    producers = []
    person_url = f"{base_url}/v0/subjects/{subject_id}/persons"
    r = requests.get(person_url, headers=headers)
    for role in r.json():
        if "relation" in role:
            if role["relation"] == "作者":
                authors.append((role["id"], role["name"]))
            elif role["relation"] == "插图":
                illustrators.append((role["id"], role["name"]))
            elif role["relation"] == "出版社":
                producers.append((role["id"], role["name"]))
    return {"authors": authors, "illustrators": illustrators, "producers": producers}
