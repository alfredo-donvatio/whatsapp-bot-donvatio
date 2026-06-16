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
OCR_URL = os.getenv("OCR_URL", "http://10.20.1.82:8000")
OCR_API_KEY = os.getenv("OCR_API_KEY")

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
        "instancia": "don-vatio-nuevo",
        "asesor_incluido": True,
        "email": "zaira@donvatio.es"
    },
}

estados = {}

def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def create_token(user_id: int) -> str:
    issued_at = int(time.time())
    payload = {
        "idUsuario": user_id,
        "scopes": ["facturas:write", "comparativas:read", "contratos:write"],
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

def enviar_imagen(numero: str, instancia: str, ruta_imagen: str, caption: str = ""):
        url = f"{EVOLUTION_URL}/message/sendMedia/{instancia}"
        headers = {"apikey": API_KEY}
        with open(ruta_imagen, "rb") as f:
            image_bytes = f.read()
        body = {
            "number": numero,
            "mediatype": "image",
            "caption": caption,
            "media": base64.b64encode(image_bytes).decode()
        }
        requests.post(url, json=body, headers=headers)

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    import threading
    print("🚀 Startup event ejecutándose...")
    threading.Thread(target=configurar_webhook, daemon=True).start()
    yield
app = FastAPI(lifespan=lifespan)

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
        estados[numero] = {"paso": "esperando_factura", "email": ""}
        enviar_mensaje(numero, instancia, f"¡Hola! 👋 Soy el asistente de {nombre_colaborador}.⚡\n\nEstoy aquí para ayudarte a ahorrar en tu factura de luz o gas. ⚡⛽\n\nEnvíame tu factura y en segundos te diré cuánto puedes ahorrar. 😎")

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "esperando_factura":
        email = colaborador.get("email", "")
        try:
            download_url = f"{EVOLUTION_URL}/chat/getBase64FromMediaMessage/{instancia}"
            headers = {"apikey": API_KEY, "Content-Type": "application/json"}
            download_response = requests.post(
                download_url,
                json={"message": data["data"], "convertToMp4": False},
                headers=headers
            )
            base64_data = download_response.json()["base64"]
            pdf_bytes = base64.b64decode(base64_data)

            user_id = await get_user_id(subdominio)
            token = create_token(user_id)

            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f"{DV_URL}/api/envio_facturas",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"email": email, "telefono": numero.replace("@s.whatsapp.net", "")},
                    files={"files": ("factura.pdf", pdf_bytes, "application/pdf")},
                    data={"agente": str(user_id), "terminos": "true"}
                )

            resultado = response.json()
            print(f"Resultado completo API: {resultado}")
            ahorro = resultado.get("comparativa", {}).get("ahorro")
            hay_ahorro = ahorro is not None and ahorro > 0

            

            titular = resultado.get("comparativa", {}).get("titular", "")
            print(f"AHORRO: {resultado.get('comparativa', {}).get('ahorro')}")
            print(f"OPCIONES: {resultado.get('comparativa', {}).get('opciones', [])[:1]}")
            asesor = colaborador.get("asesor_incluido", False)

            estados[numero] = {
                "paso": "pregunta_tramitar" if hay_ahorro else "pregunta_asesor_sin_ahorro",
                "asesor_incluido": asesor,
                "email": email,
                "factura_bytes": base64.b64encode(pdf_bytes).decode(),
                "titular": titular,
                "direccion": resultado["comparativa"]["direccion"],
                "poblacion": resultado["comparativa"]["poblacion"],
                "provincia": resultado["comparativa"]["provincia"],
                "cp": resultado["comparativa"]["cp"],
                "telefono": resultado["comparativa"]["telefono"],
                "idComparativa": resultado["comparativa"]["idComparativa"],
                "idTarifaComparativa": resultado["comparativa"].get("situacion_actual", {}).get("idTarifaComparativa"),
                "idFactura": resultado["comparativa"].get("situacion_actual", {}).get("idFactura"),
                "tipo_simulacion": resultado["comparativa"].get("situacion_actual", {}).get("tipo_simulacion"),
            }

            if hay_ahorro:
                enviar_mensaje(numero, instancia, f"✅ Factura procesada, {titular}.\n\n💡 Podrías ahorrar *{ahorro}€ al año* cambiando de tarifa.")
                
                opciones = resultado.get("comparativa", {}).get("opciones", [])
                if opciones:
                    medallas = ["🥇", "🥈", "🥉"]
                    top_tarifas = "🌟Top Tarifas🌟\n"
                    for i, opcion in enumerate(opciones[:3]):
                        nombre_comercializadora = opcion.get("comercializadora", {}).get("nombre", "")
                        ahorro_cliente = opcion.get("ahorro_cliente")
                        if ahorro_cliente is not None:
                            top_tarifas += f"{medallas[i]}*{nombre_comercializadora}* - *{abs(ahorro_cliente)}€/año*\n"
                        else:
                            top_tarifas += f"{medallas[i]}*{nombre_comercializadora}*\n"
                    enviar_mensaje(numero, instancia, top_tarifas)
            else:
                enviar_mensaje(numero, instancia, f"✅ Factura procesada, {titular}.")

            # Mensaje según ahorro
            if hay_ahorro:
                estados[numero]["paso"] = "pregunta_tramitar"
                enviar_mensaje(numero, instancia, "¿Deseas tramitar tu factura ahora?\n\nPor favor responde con *SI* o *NO*")
            else:
                estados[numero]["paso"] = "pregunta_asesor_sin_ahorro"
                enviar_mensaje(numero, instancia, "💡 ¿Deseas que te contacte un asesor para ver qué opciones tienes?\n\nPor favor responde con *SI* o *NO*")
        
        except Exception as e:
            print(f"Error procesando factura: {e}")
            enviar_mensaje(numero, instancia, "❌ No he podido leer tu factura. Por favor envíame el PDF de tu factura de luz.")
            estados[numero]["paso"] = "esperando_factura"
                 
    
               
    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "pregunta_tramitar":
        if mensaje.upper() == "SI":
            estados[numero]["paso"] = "aviso_contratacion"
            enviar_mensaje(numero, instancia, """ℹ️ En el siguiente paso vas a cambiar tu tarifa eléctrica. Es un proceso que suele tardar 2-3 días. Para ello necesitaremos:

    👉 email
    👉 teléfono
    👉 IBAN
    👉 dos fotos del DNI (anverso, reverso)

    ¿Tienes la información a mano y deseas continuar? SI / NO""")
        elif mensaje.upper() == "NO":
            estados[numero] = "inicio"
            enviar_mensaje(numero, instancia, "De acuerdo. Si cambias de opinión escríbenos. ¡Hasta pronto! 👋")
        else:
            enviar_mensaje(numero, instancia, "Por favor responde *SI* o *NO*.")

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "pregunta_asesor_sin_ahorro":
        if mensaje.upper() == "SI":
            estados[numero]["paso"] = "pedir_email_sin_ahorro"
            enviar_mensaje(numero, instancia, "¿Cuál es tu email de contacto?")
        elif mensaje.upper() == "NO":
            estados[numero] = "inicio"
            enviar_mensaje(numero, instancia, "De acuerdo. Si cambias de opinión escríbenos. ¡Hasta pronto! 👋")
        else:
            enviar_mensaje(numero, instancia, "Por favor responde *SI* o *NO*.")
    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "pedir_email_sin_ahorro":
        import re
        if not re.match(r"[^@]+@[^@]+\.[^@]+", mensaje):
            enviar_mensaje(numero, instancia, "❌ Email no válido. Por favor escribe un email correcto.\n\nEjemplo: nombre@gmail.com")
        else:
            estados[numero]["email_contacto"] = mensaje
            telefono_factura = estado_actual.get("telefono", "")
            estados[numero]["paso"] = "confirmar_telefono_sin_ahorro"
            enviar_mensaje(numero, instancia, f"Tu teléfono es el {telefono_factura}, ¿verdad? Responde *SI* o *NO*")

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "confirmar_telefono_sin_ahorro":
        if mensaje.upper() == "SI":
            estados[numero]["telefono_contacto"] = estado_actual.get("telefono", "")
            estados[numero]["paso"] = "confirmar_datos_sin_ahorro"
            e = estados[numero]
            resumen = (
                f"📋 *Resumen de tus datos:*\n\n"
                f"👤 Titular: {e['titular']}\n"
                f"📞 Teléfono: {e['telefono_contacto']}\n"
                f"📧 Email: {e['email_contacto']}\n\n"
                f"¿Son correctos? Responde *SI* para confirmar o *NO* para cancelar."
            )
            enviar_mensaje(numero, instancia, resumen)
        elif mensaje.upper() == "NO":
            estados[numero]["paso"] = "pedir_telefono_sin_ahorro"
            enviar_mensaje(numero, instancia, "Por favor dime tu teléfono de contacto.")
        else:
            enviar_mensaje(numero, instancia, "Por favor responde *SI* o *NO*.")

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "pedir_telefono_sin_ahorro":
        
        telefono = mensaje.replace(" ", "").replace("+34", "")
        if not telefono.isdigit() or len(telefono) != 9 or not telefono.startswith(("6", "7", "9")):
            enviar_mensaje(numero, instancia, "❌ Teléfono no válido. Por favor escribe un número de teléfono correcto.\n\nEjemplo: 612345678")
        else:
            estados[numero]["telefono_contacto"] = mensaje
            estados[numero]["paso"] = "confirmar_datos_sin_ahorro"
            e = estados[numero]
            resumen = (
                f"📋 *Resumen de tus datos:*\n\n"
                f"👤 Titular: {e['titular']}\n"
                f"📞 Teléfono: {e['telefono_contacto']}\n"
                f"📧 Email: {e['email_contacto']}\n\n"
                f"¿Son correctos? Responde *SI* para confirmar o *NO* para cancelar."
            )
            enviar_mensaje(numero, instancia, resumen)

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "confirmar_datos_sin_ahorro":
        if mensaje.upper() == "SI":
            enviar_mensaje(numero, instancia, "✅ Perfecto. Un asesor se pondrá en contacto contigo pronto. ¡Hasta pronto! 👋")
            enviar_imagen(numero, instancia, "/app/assets/dv_adios.png", "¡Hasta pronto!")
            estados[numero] = "inicio"
        elif mensaje.upper() == "NO":
            estados[numero] = "inicio"
            enviar_mensaje(numero, instancia, "De acuerdo. Si quieres volver a intentarlo escribe *hola*. ¡Hasta pronto! 👋")
            enviar_imagen(numero, instancia, "/app/assets/dv_adios.png", "¡Hasta pronto!")
        else:
            enviar_mensaje(numero, instancia, "Por favor responde *SI* o *NO*.")
   
    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "aviso_contratacion":
        if mensaje.upper() == "SI":
            telefono_factura = estado_actual.get("telefono", "")
            estados[numero]["paso"] = "confirmar_telefono"
            enviar_mensaje(numero, instancia, f"Tu teléfono es el {telefono_factura}, ¿verdad? Responde *SI* o *NO*")
        elif mensaje.upper() == "NO":
            estados[numero] = "inicio"
            enviar_mensaje(numero, instancia, "De acuerdo. Si cambias de opinión escríbenos. ¡Hasta pronto! 👋")
        else:
            enviar_mensaje(numero, instancia, "Por favor responde *SI* o *NO*.")

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "confirmar_telefono":
        if mensaje.upper() == "SI":
            estados[numero]["telefono_contacto"] = estado_actual.get("telefono", "")
            estados[numero]["paso"] = "pedir_email_alta"
            enviar_mensaje(numero, instancia, "¿Cuál es tu email de contacto?")
        elif mensaje.upper() == "NO":
            estados[numero]["paso"] = "pedir_telefono_alta"
            enviar_mensaje(numero, instancia, "Por favor dime tu teléfono de contacto.")
        else:
            enviar_mensaje(numero, instancia, "Por favor responde *SI* o *NO*.")

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "pedir_telefono_alta":
        telefono = mensaje.replace(" ", "").replace("+34", "")
        if not telefono.isdigit() or len(telefono) != 9 or not telefono.startswith(("6", "7", "9")):
            enviar_mensaje(numero, instancia, "❌ Teléfono no válido. Por favor escribe un número de 9 dígitos.\n\nEjemplo: 612345678")
        else:
            estados[numero]["telefono_contacto"] = mensaje
            estados[numero]["paso"] = "pedir_email_alta"
            enviar_mensaje(numero, instancia, "¿Cuál es tu email de contacto?")

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "pedir_email_info":

        enviar_mensaje(numero, instancia, "✅ Gracias. En breves nos pondremos en contacto contigo. ¡Hasta pronto! 👋")
        estados[numero] = "inicio"

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "pedir_email_alta":
        import re
        if not re.match(r"[^@]+@[^@]+\.[^@]+", mensaje):
            enviar_mensaje(numero, instancia, "❌ Email no válido. Por favor escribe un email correcto.\n\nEjemplo: nombre@gmail.com")
        else:
            estados[numero]["email_contacto"] = mensaje
            estados[numero]["paso"] = "pedir_iban"
            enviar_mensaje(numero, instancia, "Por favor dime tu IBAN (cuenta bancaria).\n\nEjemplo: ES91 2100 0418 4502 0005 1332")

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "pedir_iban":
        iban = mensaje.replace(" ", "").upper()
        if not iban.startswith("ES") or len(iban) != 24:
            enviar_mensaje(numero, instancia, "❌ IBAN no válido. Debe empezar por ES y tener 24 caracteres.\n\nEjemplo: ES91 2100 0418 4502 0005 1332")
        else:
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
            enviar_mensaje(numero, instancia, "❌ No pude recibir la imagen. Por favor envíame una foto del DNI por el anverso (parte delantera).")

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
            dni_anverso_bytes = base64.b64decode(estados[numero]["dni_anverso_base64"])
            dni_reverso_bytes = base64.b64decode(estados[numero]["dni_reverso_base64"])

            try:
                ocr_response = requests.post(
                    f"{OCR_URL}/ocr/extract",
                    headers={"X-API-Key": OCR_API_KEY},
                    files=[
                        ("images", ("dni_anverso.jpg", dni_anverso_bytes, "image/jpeg")),
                        ("image2", ("dni_reverso.jpg", dni_reverso_bytes, "image/jpeg")),
                    ],
                    timeout=60
                )
                print(f"OCR status: {ocr_response.status_code}")
                print(f"OCR respuesta: {ocr_response.text}")
                ocr_data = ocr_response.json()
                ocr_dni = ocr_data.get("dni", {})

                # Validar que el OCR extrajo datos mínimos
                if not ocr_dni.get("numero") or not ocr_dni.get("nombre"):
                    enviar_mensaje(numero, instancia, "❌ No he podido leer el DNI. Por favor envía una foto más clara del anverso.")
                    estados[numero]["paso"] = "pedir_dni_anverso"
                    estados[numero].pop("dni_anverso_base64", None)
                    estados[numero].pop("dni_reverso_base64", None)
                    return


            except Exception as e:
                print(f"OCR error: {e}")
                ocr_data = {"dni": {"nombre": "", "apellidos": "", "numero": ""}}
                        
                    
            
            
            print(f"OCR resultado: {ocr_data}")
            
            estados[numero]["ocr_data"] = ocr_data
            estados[numero]["paso"] = "confirmar_datos"

            ocr_dni = estados[numero]["ocr_data"].get("dni", {})
            nombre_completo = f"{ocr_dni.get('nombre', '')} {ocr_dni.get('apellidos', '')}"
            numero_dni = ocr_dni.get('numero', '')

            estado = estados[numero]
            resumen = (
                f"📋 *Resumen de tus datos:*\n\n"
                f"👤 Nombre: {nombre_completo}\n"
                f"🪪 DNI: {numero_dni}\n"
                f"📍 Dirección: {estado['direccion']}, {estado['poblacion']}, {estado['provincia']}\n"
                f"📞 Teléfono: {estado['telefono_contacto']}\n"
                f"📧 Email: {estado['email_contacto']}\n"
                f"🏦 IBAN: {estado['iban']}\n\n"
                f"¿Son correctos? Responde *SI* para confirmar o *NO* para cancelar."
            )
            enviar_mensaje(numero, instancia, resumen)
            
        except Exception as e:
            print(f"Error recibiendo DNI reverso: {e}")
            enviar_mensaje(numero, instancia, "❌ No pude recibir la imagen. Por favor envíame una foto del DNI por el reverso (parte trasera).")
            estados[numero]["paso"] = "pedir_dni_reverso"

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "confirmar_datos":
        if mensaje.upper() == "SI":
            try:
                e = estados[numero]
                ocr_dni = e["ocr_data"].get("dni", {})
                user_id = await get_user_id(subdominio)
                token = create_token(user_id)

                dni_anverso_bytes = base64.b64decode(e["dni_anverso_base64"])
                dni_reverso_bytes = base64.b64decode(e["dni_reverso_base64"])

                async with httpx.AsyncClient(timeout=60) as client:
                    response = await client.post(
                        f"{DV_URL}/api/contratos/nuevo",
                        headers={"Authorization": f"Bearer {token}"},
                        data={
                            "dni_titular": ocr_dni.get("numero", ""),
                            "nombre_titular": f"{ocr_dni.get('nombre', '')} {ocr_dni.get('apellidos', '')}",
                            "dni_firmante": ocr_dni.get("numero", ""),
                            "nombre_firmante": f"{ocr_dni.get('nombre', '')} {ocr_dni.get('apellidos', '')}",
                            "cp_cups": e["cp"],
                            "poblacion_cups": e["poblacion"],
                            "direccion_cups": e["direccion"],
                            "provincia_cups": e["provincia"],
                            "telefono": e["telefono_contacto"],
                            "email": e["email_contacto"],
                            "cuenta_bancaria": e["iban"].replace(" ", ""),
                            "idTarifaComparativa": e["idTarifaComparativa"],
                            "idComparativa": e["idComparativa"],
                            "idFactura": e["idFactura"],
                            "tipo_simulacion": e["tipo_simulacion"],
                            "id_usuario_campaign": str(user_id),
                        },
                        files={
                            "dni": ("dni_anverso.jpg", dni_anverso_bytes, "image/jpeg"),
                            "dni_reverso": ("dni_reverso.jpg", dni_reverso_bytes, "image/jpeg"),
                            "factura": ("factura.pdf", base64.b64decode(e["factura_bytes"]), "application/pdf"),
                        }
                    )

                print(f"Contratación: {response.status_code} {response.text}")
                
                # Mensaje de confirmación
                enviar_mensaje(numero, instancia, "✅ ¡Solicitud enviada correctamente! Un agente revisará tu contratación y te contactará pronto. ¡Gracias!")
                enviar_imagen(numero, instancia, "/app/assets/dv_adios.png", "¡Hasta pronto!")

            except Exception as e:
                print(f"Error contratación: {e}")
                enviar_mensaje(numero, instancia, "Ha ocurrido un error. Por favor contacta con nosotros directamente.")
            estados[numero] = "inicio"

        elif mensaje.upper() == "NO":
            estados[numero] = "inicio"
            enviar_mensaje(numero, instancia, "De acuerdo. Si quieres volver a intentarlo escribe *hola*. ¡Hasta pronto! 👋")
        else:
            enviar_mensaje(numero, instancia, "Por favor responde *SI* para confirmar o *NO* para cancelar.")
    
    