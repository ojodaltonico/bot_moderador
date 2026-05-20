# Script para desplegar cambios en Raspberry Pi
$raspIP = "192.168.0.103"
$raspUser = "raspbery"
$raspPass = "pi"
$projectPath = "/home/raspbery/services/bot_moderador"

# Crear credencial
$securePass = ConvertTo-SecureString $raspPass -AsPlainText -Force
$credential = New-Object System.Management.Automation.PSCredential($raspUser, $securePass)

# Comandos a ejecutar en la Raspberry
$commands = @(
    "cd $projectPath",
    "git pull origin HEAD",
    "source venv/bin/activate",
    "pkill -f 'uvicorn app.main' || true",
    "nohup python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > api.log 2>&1 &",
    "sleep 2",
    "cd whatsapp",
    "git pull origin HEAD",
    "pkill -f index.js || true",
    "nohup /usr/bin/node index.js > whatsapp.log 2>&1 &",
    "sleep 3",
    "echo '✅ Despliegue completado'"
)

Write-Host "🚀 Actualizando Raspberry Pi en $raspIP..." -ForegroundColor Green

try {
    $session = New-PSSession -HostName $raspIP -UserName $raspUser -Credential $credential -ErrorAction Stop
    
    foreach ($cmd in $commands) {
        Write-Host "▶ $cmd" -ForegroundColor Cyan
        Invoke-Command -Session $session -ScriptBlock { param($c) bash -c "$c" } -ArgumentList $cmd
    }
    
    Write-Host "`n✅ Despliegue completado exitosamente" -ForegroundColor Green
    
    # Ver últimas líneas de logs
    Write-Host "`n📋 Últimos logs de API:" -ForegroundColor Yellow
    Invoke-Command -Session $session -ScriptBlock { tail -n 20 /home/raspbery/services/bot_moderador/api.log }
    
    Write-Host "`n📋 Últimos logs de WhatsApp:" -ForegroundColor Yellow
    Invoke-Command -Session $session -ScriptBlock { tail -n 20 /home/raspbery/services/bot_moderador/whatsapp/whatsapp.log }
    
    Remove-PSSession $session
} catch {
    Write-Host "❌ Error: $_" -ForegroundColor Red
}
