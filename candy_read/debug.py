import requests
r = requests.get("http://10.159.219.196/ping", timeout=2)
print(r.status_code, r.text)