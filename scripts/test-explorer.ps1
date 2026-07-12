$body = '{"path":"E:\\Desktop\\agent"}'
$r = Invoke-WebRequest -Uri 'http://127.0.0.1:58080/api/explorer/open' -Method Post -Body $body -ContentType 'application/json' -UseBasicParsing -TimeoutSec 30
$j = $r.Content | ConvertFrom-Json
$root = $j.tree[0]
Write-Host ("root name: " + $root.name)
Write-Host ("children count: " + $root.children.Count)
Write-Host "--- directories ---"
$root.children | Where-Object { $_.type -eq 'directory' } | ForEach-Object { Write-Host $_.name }
Write-Host "--- files ---"
$root.children | Where-Object { $_.type -eq 'file' } | ForEach-Object { Write-Host $_.name }
