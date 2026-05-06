from fastapi import FastAPI, Request
import requests
import httpx
import time
import base64 #descarga y procesado de Pdf
import hashlib #crea hashes (huellas únicas de datos)
import hmac #crea firmas seguras
import json
from dotenv import load_dotenv
import os

load_dotenv()

app = FastAPI()

EVOLUTION_URL = os.getenv("EVOLUTION_URL", "http://evolution_api_vatio:8080")
API_KEY = os.getenv("API_KEY", "donvatio_secret_key_123")
print(f"API_KEY: {API_KEY}")
print(f"EVOLUTION_URL: {EVOLUTION_URL}")
DV_URL = os.getenv("DV_URL", "https://app.donvatio.es")
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_EXP_MINUTES = int(os.getenv("JWT_EXP_MINUTES", "60"))

colaboradores = {
    "don-vatio": {
        "subdominio": "https://zairabovino.campaigndonvatio.es/",
        "nombre": "Test oficina Alfredo",
        "instancia": "don-vatio"
    },
}

estados = {}

def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def create_token(user_id: int) -> str:
    issued_at = int(time.time())
    payload = {
        "idUsuario": user_id,
        "scopes": ["facturas:write", "comparativas:read"],
        "iat": issued_at,
        "exp": issued_at + (JWT_EXP_MINUTES * 60),
    }
    header = {"alg": "HS256", "typ": "JWT"}
    encoded_header = b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    encoded_payload = b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{encoded_header}.{encoded_payload}".encode()
    digest = hmac.new(JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
    signature = b64url_encode(digest)
    return f"{encoded_header}.{encoded_payload}.{signature}"

async def get_user_id(subdominio: str) -> int:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{DV_URL}/api/env",
            headers={"referer": subdominio}
        )
    data = response.json()
    return int(data["theme"]["id_usuario_campaign"])

def enviar_mensaje(numero: str, instancia: str, texto: str):
    url = f"{EVOLUTION_URL}/message/sendText/{instancia}"
    headers = {"apikey": API_KEY, "Content-Type": "application/json"}
    body = {"number": numero, "text": texto}
    print(f"Enviando a {url} → {numero}")
    response = requests.post(url, json=body, headers=headers)
    print(f"Respuesta: {response.status_code} {response.text}")

@app.post("/webhook/message")
async def recibir_mensaje(request: Request):
    data = await request.json()

    if data["data"]["key"]["fromMe"]:
        return {"status": "ok"}

    instancia = data["instance"]
    numero = data["data"]["key"]["remoteJid"]
    mensaje = data["data"]["message"].get("conversation", "").strip()
    
    colaborador = colaboradores.get(instancia)
    if not colaborador:
        return {"status": "ok"}

    subdominio = colaborador["subdominio"]
    nombre_colaborador = colaborador["nombre"]
    estado_actual = estados.get(numero, "inicio")

    print(f"[{instancia}][{numero}] Estado: {estado_actual} | Mensaje: {mensaje}")

    if estado_actual == "inicio" or mensaje.lower() in ["hola", "buenas", "buenos dias", "menu", "inicio"]:
        estados[numero] = "esperando_opcion"
        enviar_mensaje(numero, instancia, f"""¡Hola! 👋 Soy el asistente de {nombre_colaborador}.

¿En qué puedo ayudarte?

1️⃣ Comparar mi factura de luz
2️⃣ Recibir asesoramiento personalizado
3️⃣ Hablar con un asesor

Responde con el número de tu elección.""")

    elif estado_actual == "esperando_opcion":
        if mensaje == "1":
            estados[numero] = "esperando_email_factura"
            enviar_mensaje(numero, instancia, "Por favor, dime tu email para enviarte los resultados.")
        elif mensaje == "2":
            estados[numero] = "esperando_email_recordatorio"
            enviar_mensaje(numero, instancia, "Por favor, dime tu email y te enviaremos información personalizada.")
        elif mensaje == "3":
            estados[numero] = "esperando_datos_llamada"
            enviar_mensaje(numero, instancia, "Por favor, dime tu nombre y teléfono de contacto.\n\nEjemplo: Juan García, 612345678")
        else:
            enviar_mensaje(numero, instancia, "Por favor responde con 1, 2 o 3.")

    elif estado_actual == "esperando_email_factura":
        estados[numero] = {"paso": "esperando_factura", "email": mensaje}
        enviar_mensaje(numero, instancia, "Perfecto. Ahora envíame una foto de tu factura de luz.")

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "esperando_factura":
        email = estado_actual["email"]
        message_id = data["data"]["key"]["id"]
    
        try:
            # 1. Descargar PDF desde Evolution API
            download_url = f"{EVOLUTION_URL}/chat/getBase64FromMediaMessage/{instancia}"
            headers = {"apikey": API_KEY, "Content-Type": "application/json"}
            download_response = requests.post(
                download_url,
                json={"message": data["data"], "convertToMp4": False},
                headers=headers
            )
            base64_data = download_response.json()["base64"]
            
            # 2. Convertir base64 a bytes
            import base64
            pdf_bytes = base64.b64decode(base64_data)
            
            # 3. Obtener token Don Vatio
            user_id = await get_user_id(subdominio)
            token = create_token(user_id)
            
            # 4. Enviar a API Don Vatio
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f"{DV_URL}/api/envio_facturas",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"email": email, "telefono": numero.replace("@s.whatsapp.net", "")},
                    files={"files": ("factura.pdf", pdf_bytes, "application/pdf")},
                    data={"agente": str(user_id), "terminos": "true"}
                )
            
            resultado = response.json()
            print(f"Resultado API: {resultado}")
            
            if resultado.get("estado") == "RECHAZADA":
                enviar_mensaje(numero, instancia, "❌ No hemos podido procesar tu factura. ¿Puedes enviarnos una imagen más clara?")
            else:
                ahorro = resultado.get("comparativa", {}).get("ahorro", 0)
                titular = resultado.get("comparativa", {}).get("titular", "")

                enviar_mensaje(numero, instancia, f"✅ Factura procesada, {titular}.\n\n"f"💡 Podrías ahorrar *{ahorro}€ al año* cambiando de tarifa.\n\n"
    f"Un asesor se pondrá en contacto contigo pronto para explicarte las opciones."
)
            
        except Exception as e:
            print(f"Error procesando factura: {e}")
            enviar_mensaje(numero, instancia, "Ha ocurrido un error procesando tu factura. Por favor inténtalo más tarde.")
        
        estados[numero] = "inicio"

    elif estado_actual == "esperando_email_recordatorio":
        try:
            user_id = await get_user_id(subdominio)
            token = create_token(user_id)
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{DV_URL}/api/recuerdamelo",
                    headers={"Authorization": f"Bearer {token}"},
                    data={"email": mensaje, "agente": str(user_id)}
                )
            enviar_mensaje(numero, instancia, f"✅ Perfecto, te enviaremos información a {mensaje}. ¡Hasta pronto!")
        except Exception as e:
            print(f"Error API: {e}")
            enviar_mensaje(numero, instancia, "Ha ocurrido un error. Por favor inténtalo más tarde.")
        estados[numero] = "inicio"

    elif estado_actual == "esperando_datos_llamada":
        try:
            partes = mensaje.split(",")
            nombre = partes[0].strip()
            telefono = partes[1].strip() if len(partes) > 1 else ""
            user_id = await get_user_id(subdominio)
            token = create_token(user_id)
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{DV_URL}/api/llamamos",
                    headers={"Authorization": f"Bearer {token}"},
                    data={"nombre": nombre, "telefono": telefono, "agente": str(user_id)}
                )
            enviar_mensaje(numero, instancia, f"✅ Perfecto {nombre}, un asesor se pondrá en contacto contigo en {telefono}. ¡Hasta pronto!")
        except Exception as e:
            print(f"Error API: {e}")
            enviar_mensaje(numero, instancia, "Ha ocurrido un error. Por favor inténtalo más tarde.")
        estados[numero] = "inicio"

    return {"status": "ok"}