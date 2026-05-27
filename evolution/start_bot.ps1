cd "C:\Users\usuario\Desktop\don vatio\whatsapp integracion colaboradores\evolution"
docker-compose -f docker-compose.yml up -d
docker-compose -f docker-fastapi.yml up -d
Start-Sleep -Seconds 10
Invoke-WebRequest -Uri "http://localhost:8080/webhook/set/don-vatio-nuevo" -Method POST -Headers @{"apikey"="donvatio_secret_key_123"; "Content-Type"="application/json"} -Body '{"webhook":{"enabled":true,"url":"http://fastapi_bot:8000/webhook/message","webhookByEvents":false,"webhookBase64":false,"events":["MESSAGES_UPSERT"]}}'
Write-Host "Bot listo"