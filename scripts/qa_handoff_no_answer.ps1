# QA: dispara la plantilla "no contesto" (Bloque 7) sin necesitar una llamada real.
# Uso (PowerShell):
#   ./scripts/qa_handoff_no_answer.ps1 -Phone "+573123528153" -Nombre "Juan Perez" -Poliza "POL-12345"
#   ./scripts/qa_handoff_no_answer.ps1 -Phone "+573123528153" -Nombre "Juan Perez" -Poliza "POL-12345" -Documento "900144220"
#
# -Documento es OPCIONAL:
#   CON  -> el bot NO pide documento, entra directo a la poliza (caso 7.68).
#   SIN  -> saluda con contexto pero pide documento para confirmar identidad.
param(
  [Parameter(Mandatory=$true)][string]$Phone,
  [Parameter(Mandatory=$true)][string]$Nombre,
  [Parameter(Mandatory=$true)][string]$Poliza,
  [string]$Documento = ""
)

$Base  = "https://landa-agent-service-production.up.railway.app"
$Token = "gbqyAHpxvJl-PS2jOxeuj20hXyTwuIjWWrfc1rKWCVTwl7y7YLH7fKG5fgVEtZqC"
$CaseId = [guid]::NewGuid().ToString()

$payload = @{
  phone         = $Phone
  cliente_nombre = $Nombre
  numero_poliza = $Poliza
  case_id       = $CaseId
}
if ($Documento -ne "") { $payload.documento = $Documento }
$Body = $payload | ConvertTo-Json -Compress

Write-Host "case_id=$CaseId"
Write-Host "POST $Base/case/handoff/no_answer"
Write-Host "body=$Body"
Write-Host "---"
try {
  $resp = Invoke-RestMethod -Method Post -Uri "$Base/case/handoff/no_answer" `
    -Headers @{ Authorization = "Bearer $Token" } `
    -ContentType "application/json" -Body $Body
  $resp | ConvertTo-Json -Compress | Write-Host
} catch {
  Write-Host "ERROR: $($_.Exception.Message)"
  if ($_.ErrorDetails) { Write-Host $_.ErrorDetails.Message }
}
Write-Host "---"
Write-Host "Espera la plantilla en WhatsApp ($Phone). Toca 'Si, ayudenme' o 'Mas tarde'."
Write-Host "Para probar idempotencia, vuelve a correr con el MISMO case_id (debe dar sent=false)."
