$bytes = [System.IO.File]::ReadAllBytes('graphify-out\.graphify_detect.json')
$utf8 = [System.Text.Encoding]::UTF8.GetString($bytes)
[System.IO.File]::WriteAllText('graphify-out\.graphify_detect_utf8.json', $utf8, [System.Text.Encoding]::UTF8)
Write-Host "Done"
