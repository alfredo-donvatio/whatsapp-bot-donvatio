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
INSTANCE = os.getenv("INSTANCE", "don-vatio-nuevo")

def configurar_webhook():
    """Configura el webhook en Evolution API al arrancar"""
    time.sleep(5)  # Espera a que Evolution API esté lista
    url = f"{EVOLUTION_URL}/webhook/set/{INSTANCE}"
    headers = {"apikey": API_KEY, "Content-Type": "application/json"}
    body = {
        "webhook": {
            "enabled": True,
            "url": "http://172.18.0.2:8000/webhook/message",
            "webhookByEvents": False,
            "webhookBase64": False,
            "events": ["MESSAGES_UPSERT"]
        }
    }
    try:
        response = requests.post(url, json=body, headers=headers)
        print(f"Webhook configurado: {response.status_code}")
    except Exception as e:
        print(f"Error configurando webhook: {e}")


colaboradores = {
    "don-vatio-nuevo": {
        "subdominio": "https://zairabovino.campaigndonvatio.es/",
        "nombre": "Test oficina Alfredo",
        "instancia": "don-vatio-nuevo"
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

@app.on_event("startup")
async def startup_event():
    import threading
    threading.Thread(target=configurar_webhook, daemon=True).start()

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
        print(f"ESTADO ACTUAL: {estado_actual}")
        
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

            # Guardar datos para contratación
            estados[numero] = {
                "paso": "pregunta_alta",
                "email": email,
                "titular": resultado["comparativa"]["titular"],
                "direccion": resultado["comparativa"]["direccion"],
                "poblacion": resultado["comparativa"]["poblacion"],
                "provincia": resultado["comparativa"]["provincia"],
                "cp": resultado["comparativa"]["cp"],
                "telefono": resultado["comparativa"]["telefono"],
                "idComparativa": resultado["comparativa"]["idComparativa"],
                "idTarifaComparativa": resultado["comparativa"]["situacion_actual"]["idTarifaComparativa"],
                "idFactura": resultado["comparativa"]["situacion_actual"]["idFactura"],
                "tipo_simulacion": resultado["comparativa"]["situacion_actual"]["tipo_simulacion"],
            }
            
            if resultado.get("estado") == "RECHAZADA":
                enviar_mensaje(numero, instancia, "❌ No hemos podido procesar tu factura. ¿Puedes enviarnos una imagen más clara?")
            else:
                ahorro = resultado.get("comparativa", {}).get("ahorro", 0)
                titular = resultado.get("comparativa", {}).get("titular", "")

                enviar_mensaje(numero, instancia, f"✅ Factura procesada, {titular}.\n\n"f"💡 Podrías ahorrar *{ahorro}€ al año* cambiando de tarifa.\n\n"
    f"¿Te interesa darte de alta en nuestra tarifa?Responde *SI* o *NO*."
)
            
        except Exception as e:
            print(f"Error procesando factura: {e}")
            enviar_mensaje(numero, instancia, "Ha ocurrido un error procesando tu factura. Por favor inténtalo más tarde.")
            estados[numero] = "inicio"

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "pregunta_alta":
        if mensaje.upper() == "SI":
            estados[numero]["paso"] = "pregunta_tipo_cliente"
            enviar_mensaje(numero, instancia, "¿Eres particular o empresa?\n\n1️⃣ Particular\n2️⃣ Empresa")
        elif mensaje.upper() == "NO":
            estados[numero] = "inicio"
            enviar_mensaje(numero, instancia, "De acuerdo, si cambias de opinión escríbenos. ¡Hasta pronto! 👋")
        else:
            enviar_mensaje(numero, instancia, "Por favor responde *SI* o *NO*.")

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "pregunta_tipo_cliente":
        if mensaje == "1":
            estados[numero]["paso"] = "pedir_telefono_alta"
            estados[numero]["tipo_cliente"] = "particular"
            enviar_mensaje(numero, instancia, "Por favor dime tu número de teléfono de contacto.")
        elif mensaje == "2":
            estados[numero]["paso"] = "pedir_telefono_alta"
            estados[numero]["tipo_cliente"] = "empresa"
            enviar_mensaje(numero, instancia, "Por favor dime tu número de teléfono de contacto.")
        else:
            enviar_mensaje(numero, instancia, "Por favor responde *1* para particular o *2* para empresa.")

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "pedir_telefono_alta":
        estados[numero]["telefono_contacto"] = mensaje
        estados[numero]["paso"] = "pedir_email_alta"
        enviar_mensaje(numero, instancia, "¿Cuál es tu email de contacto?")

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "pedir_email_alta":
        estados[numero]["email_contacto"] = mensaje
        estados[numero]["paso"] = "pedir_iban"
        enviar_mensaje(numero, instancia, "Por favor dime tu IBAN (cuenta bancaria).\n\nEjemplo: ES12 3456 7890 1234 5678 9012")

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "pedir_iban":
        estados[numero]["iban"] = mensaje
        estados[numero]["paso"] = "pedir_dni_anverso"
        enviar_mensaje(numero, instancia, "Envíame una foto del DNI por el anverso (parte delantera).")
    
    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "pedir_dni_anverso":
    # Guardar imagen anverso como base64
        try:
            download_url = f"{EVOLUTION_URL}/chat/getBase64FromMediaMessage/{instancia}"
            headers = {"apikey": API_KEY, "Content-Type": "application/json"}
            download_response = requests.post(
                download_url,
                json={"message": data["data"], "convertToMp4": False},
                headers=headers
            )
            estados[numero]["dni_anverso_base64"] = download_response.json()["base64"]
            estados[numero]["paso"] = "pedir_dni_reverso"
            enviar_mensaje(numero, instancia, "Perfecto. Ahora envíame una foto del DNI por el reverso (parte trasera).")
        except Exception as e:
            print(f"Error recibiendo DNI anverso: {e}")
            enviar_mensaje(numero, instancia, "No pude recibir la imagen. Por favor inténtalo de nuevo.")

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "pedir_dni_reverso":
        try:
            download_url = f"{EVOLUTION_URL}/chat/getBase64FromMediaMessage/{instancia}"
            headers = {"apikey": API_KEY, "Content-Type": "application/json"}
            download_response = requests.post(
                download_url,
                json={"message": data["data"], "convertToMp4": False},
                headers=headers
            )
            estados[numero]["dni_reverso_base64"] = download_response.json()["base64"]
            estados[numero]["paso"] = "confirmar_datos"

            # Mostrar resumen para confirmar
            e = estados[numero]
            resumen = (
                f"📋 *Resumen de tus datos:*\n\n"
                f"👤 Titular: {e['titular']}\n"
                f"📍 Dirección: {e['direccion']}, {e['poblacion']}, {e['provincia']}\n"
                f"📞 Teléfono: {e['telefono_contacto']}\n"
                f"📧 Email: {e['email_contacto']}\n"
                f"🏦 IBAN: {e['iban']}\n\n"
                f"¿Son correctos? Responde *SI* para confirmar o *NO* para corregir."
            )
            enviar_mensaje(numero, instancia, resumen)
        except Exception as e:
            print(f"Error recibiendo DNI reverso: {e}")
            enviar_mensaje(numero, instancia, "No pude recibir la imagen. Por favor inténtalo de nuevo.")
 
    
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

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "confirmar_datos":
        if mensaje.upper() == "SI":
            try:
                e = estados[numero]
                user_id = await get_user_id(subdominio)
                token = create_token(user_id)
                
                # Convertir base64 a bytes para los DNIs
                dni_anverso_bytes = base64.b64decode(e["dni_anverso_base64"])
                dni_reverso_bytes = base64.b64decode(e["dni_reverso_base64"])
                
                async with httpx.AsyncClient(timeout=60) as client:
                    response = await client.post(
                        f"{DV_URL}/api/contratos/nuevo",
                        headers={"Authorization": f"Bearer {token}"},
                        data={
                            "dni_titular": "",
                            "nombre_titular": e["titular"],
                            "dni_firmante": "",
                            "nombre_firmante": e["titular"],
                            "cp_cups": e["cp"],
                            "poblacion_cups": e["poblacion"],
                            "direccion_cups": e["direccion"],
                            "provincia_cups": e["provincia"],
                            "telefono": e["telefono_contacto"],
                            "email": e["email_contacto"],
                            "cuenta_bancaria": e["iban"],
                            "idTarifaComparativa": e["idTarifaComparativa"],
                            "idComparativa": e["idComparativa"],
                            "idFactura": e["idFactura"],
                            "tipo_simulacion": e["tipo_simulacion"],
                            "id_usuario_campaign": str(user_id),
                        },
                        files={
                            "dni": ("dni_anverso.jpg", dni_anverso_bytes, "image/jpeg"),
                            "dni_reverso": ("dni_reverso.jpg", dni_reverso_bytes, "image/jpeg"),
                        }
                    )
                
                print(f"Contratación: {response.status_code} {response.text}")
                enviar_mensaje(numero, instancia, "✅ ¡Solicitud enviada correctamente! Un agente revisará tu contratación y te contactará pronto. ¡Gracias!")
                
            except Exception as e:
                import traceback
                print(f"Error contratación: {e}")
                print(traceback.format_exc())
                enviar_mensaje(numero, instancia, "Ha ocurrido un error. Por favor contacta con nosotros directamente.")
            estados[numero] = "inicio"
        
        elif mensaje.upper() == "NO":
            estados[numero] = "inicio"
            enviar_mensaje(numero, instancia, "De acuerdo. Si quieres volver a intentarlo escribe *hola*. ¡Hasta pronto! 👋")
        else:
            enviar_mensaje(numero, instancia, "Por favor responde *SI* para confirmar o *NO* para cancelar.")
    return {"status": "ok"}