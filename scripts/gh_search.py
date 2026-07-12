import urllib.request, json

for file_name in ["json_consensus.py", "counting_consensus.py"]:
    url = f"https://raw.githubusercontent.com/Telsho/Extrai/main/src/extrai/core/{file_name}"
    req = urllib.request.Request(url)
    try:
        content = urllib.request.urlopen(req).read().decode("utf-8")
        print(f"=== {file_name} ===")
        print(content[:2500])
        print()
    except Exception as e:
        print(f"=== {file_name}: {e} ===")
