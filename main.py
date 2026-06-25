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
import cv2
import numpy as np
from datetime import datetime


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
        "subdominio": "https://pruebascampaign.campaigndonvatio.es",
        "nombre": "Test oficina Alfredo",
        "instancia": "don-vatio-nuevo",
        "asesor_incluido": True,
        "email": "informatica@donvatio.es"
    },
}

import redis
r = redis.Redis(host='redis_vatio', port=6379, decode_responses=True)
estados = {}
mensajes_procesados = set()

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



def es_imagen_valida_documento(image_bytes):
    """Verifica si la imagen parece un documento (no borrosa, con bordes claros)"""
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
    
    if img is None:
        return False
    
    # Detectar nitidez (Laplacian variance)
    laplacian_var = cv2.Laplacian(img, cv2.CV_64F).var()
    
    # Umbral mínimo de nitidez (ajustable)
    if laplacian_var < 50:
        return False  # Imagen muy borrosa
    
    return True

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

    message_id = data["data"]["key"]["id"]
    
    if r.exists(f"msg:{message_id}"):
        return {"status": "ok"}
    r.setex(f"msg:{message_id}", 86400, "1")

    if mensaje.lower() in ["cancelar", "salir", "cancel"]:
        estados[numero] = "inicio"
        enviar_mensaje(numero, instancia, "❌ Proceso cancelado. Escribe *hola* para empezar de nuevo.")
        return {"status": "ok"}

    def enviar_mensaje_con_cancelar(numero, instancia, texto):
        texto_completo = f"{texto}\n\n_Escribe *cancelar* para volver a empezar_"
        enviar_mensaje(numero, instancia, texto_completo)
        

    if estado_actual == "inicio" or mensaje.lower() in ["hola", "buenas", "buenos dias", "menu", "inicio"]:
        estados[numero] = {"paso": "esperando_factura", "email": ""}
        enviar_mensaje_con_cancelar(numero, instancia, f"¡Hola! 👋 Soy el asistente de {nombre_colaborador}.⚡\n\nEstoy aquí para ayudarte a ahorrar en tu factura de luz o gas. ⚡⛽\n\nEnvíame tu factura en formato PDF y en segundos te diré cuánto puedes ahorrar. 😎\n\n Al subir tu factura aceptas nuestros términios y condiciones: ✅📝\n\n www.nuestrosterminosycondiciones.es")

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "esperando_factura":
        enviar_mensaje(numero, instancia, "⏳ Procesando tu factura, esto puede tardar unos segundos...")
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

            print(f"Email usado para conexión 1: {email}"),

            async with httpx.AsyncClient(timeout=90) as client:
                response = await client.post(
                    f"{DV_URL}/api/envio_facturas",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"email": email, "telefono": numero.replace("@s.whatsapp.net", "")},
                    files={"files": ("factura.pdf", pdf_bytes, "application/pdf")},
                    data={"agente": str(user_id), "terminos": "true"}
                )
                print(f"Email usado para conexión 2: {email}"),

            resultado = response.json()
            print(f"Resultado completo API: {resultado}")
            if "comparativa" not in resultado:
                print(f"Respuesta sin comparativa: {resultado}")
                enviar_mensaje(numero, instancia, "❌ No he podido leer tu factura. Por favor envíame el PDF de tu factura de luz.")
                estados[numero]["paso"] = "esperando_factura"
                return
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
            else:
                enviar_mensaje(numero, instancia, f"✅ Factura procesada, {titular}.")
                
            opciones = resultado.get("comparativa", {}).get("opciones", [])

            if hay_ahorro and opciones:
                top_3 = []
                top_3.append(opciones[0])
                primera_comercializadora = opciones[0].get("comercializadora", {}).get("nombre", "")

                for opcion in opciones[1:]:
                    nombre = opcion.get("comercializadora", {}).get("nombre", "")
                    if nombre != primera_comercializadora:
                        top_3.append(opcion)
                    if len(top_3) == 3:
                        break

                medallas = ["🥇", "🥈", "🥉"]
                numeros = ["1️⃣", "2️⃣", "3️⃣"]
                top_tarifas = "🌟Top Tarifas🌟\n\n"

                for i, opcion in enumerate(top_3):
                    nombre_comercializadora = opcion.get("comercializadora", {}).get("nombre", "")
                    nombre_producto = opcion.get("nombre", "")
                    ahorro_cliente = opcion.get("ahorro_cliente")
                    precios = opcion.get("precios", {})
                    
                    top_tarifas += f"{numeros[i]} {medallas[i]}*{nombre_comercializadora}* - {nombre_producto}\n"
                    if ahorro_cliente is not None:
                        top_tarifas += f"   💰 Ahorro: *{abs(ahorro_cliente)}€/año*\n"
                    if precios.get("P1"):
                        top_tarifas += f"   ⚡ Potencia: {precios.get('P1')}€\n"
                    if precios.get("E1"):
                        top_tarifas += f"   🔌 Energía: {precios.get('E1')}€/kWh\n"
                    top_tarifas += "\n"
                
                enviar_mensaje(numero, instancia, top_tarifas)

                estados[numero]["top_3_opciones"] = top_3
                enviar_mensaje(numero, instancia, "Estas son las mejores ofertas que hemos encontrado para ti. Por favor, selecciona el número de la que desees contratar.")
                estados[numero]["paso"] = "seleccionar_tarifa"

                

            elif not hay_ahorro:
                situacion = resultado.get("comparativa", {}).get("situacion_actual", {})
                importe_anual = situacion.get("importe_anual")
                importe_potencia = situacion.get("importe_potencia")
                importe_energia = situacion.get("importe_energia")
                
                mensaje_simulacion = "📊 *Simulación gasto anual:*\n\n"
                if importe_anual is not None:
                    mensaje_simulacion += f"💰 Total: *{importe_anual}€/año*\n"
                if importe_potencia is not None:
                    mensaje_simulacion += f"⚡ Potencia: {importe_potencia}€\n"
                if importe_energia is not None:
                    mensaje_simulacion += f"🔌 Energía: {importe_energia}€\n"
                
                enviar_mensaje(numero, instancia, mensaje_simulacion)    
            
            estados[numero]["paso"] = "seleccionar_tarifa"
            
        
            
        except Exception as e:
            import traceback
            print(f"Error procesando factura: {e}")
            if estados.get(numero, {}).get("paso") == "esperando_factura" or estados.get(numero) == "esperando_factura":
                enviar_mensaje(numero, instancia, "❌ No he podido leer tu factura. Por favor envíame el PDF de tu factura de luz.")
                estados[numero]["paso"] = "esperando_factura"
            print(traceback.format_exc())
                 
    
               
    

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "seleccionar_tarifa":
        if mensaje in ["1", "2", "3"]:
            idx = int(mensaje) - 1
            top_3_opciones = estado_actual.get("top_3_opciones", [])
            if idx < len(top_3_opciones):
                tarifa_elegida = top_3_opciones[idx]
                estados[numero]["tarifa_elegida"] = tarifa_elegida
                estados[numero]["idTarifaComparativa"] = tarifa_elegida.get("idTarifaComparativa")
                nombre_comercializadora = tarifa_elegida.get("comercializadora", {}).get("nombre", "")
                nombre_producto = tarifa_elegida.get("nombre", "")
                estados[numero]["paso"] = "aviso_contratacion"
                enviar_mensaje(numero, instancia, f"Has seleccionado: *{nombre_comercializadora} - {nombre_producto}*\n\nℹ️ En el siguiente paso vas a cambiar tu tarifa eléctrica. Es un proceso que suele tardar una semana. Para ello necesitaremos:\n\n👉 email\n👉 teléfono\n👉 IBAN\n👉 dos fotos del DNI (anverso, reverso)\n\n¿Tienes la información a mano y deseas continuar? SI / NO")
            else:
                enviar_mensaje(numero, instancia, "❌ Opción no válida. Por favor responde con 1, 2 o 3.")
        else:
            enviar_mensaje(numero, instancia, "❌ Por favor responde con 1, 2 o 3 para seleccionar tu tarifa.")
    
    
    
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
            enviar_mensaje_con_cancelar(numero, instancia, f"Tu teléfono es el {telefono_factura}, ¿verdad? Responde *SI* o *NO*")
        elif mensaje.upper() == "NO":
            estados[numero] = "inicio"
            enviar_mensaje(numero, instancia, "De acuerdo. Si cambias de opinión escríbenos. ¡Hasta pronto! 👋")
        else:
            enviar_mensaje(numero, instancia, "Por favor responde *SI* o *NO*.")

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "confirmar_telefono":
        if mensaje.upper() == "SI":
            estados[numero]["telefono_contacto"] = estado_actual.get("telefono", "")
            estados[numero]["paso"] = "pedir_email_alta"
            enviar_mensaje_con_cancelar(numero, instancia, "¿Cuál es tu email de contacto?")
        elif mensaje.upper() == "NO":
            estados[numero]["paso"] = "pedir_telefono_alta"
            enviar_mensaje(numero, instancia, "Por favor dime tu teléfono de contacto.")
        else:
            enviar_mensaje(numero, instancia, "Por favor responde *SI* o *NO*.")

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "pedir_telefono_alta":
        telefono = mensaje.replace(" ", "").replace("+34", "")
        if not telefono.isdigit() or len(telefono) != 9 or not telefono.startswith(("6", "7", "9")):
            enviar_mensaje_con_cancelar(numero, instancia, "❌ Teléfono no válido. Por favor escribe un número de 9 dígitos.\n\nEjemplo: 612345678")
        else:
            estados[numero]["telefono_contacto"] = mensaje
            estados[numero]["paso"] = "pedir_email_alta"
            enviar_mensaje_con_cancelar(numero, instancia, "¿Cuál es tu email de contacto?")

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "pedir_email_info":

        enviar_mensaje(numero, instancia, "✅ Gracias. En breves nos pondremos en contacto contigo. ¡Hasta pronto! 👋")
        estados[numero] = "inicio"

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "pedir_email_alta":
        import re
        if not re.match(r"[^@]+@[^@]+\.[^@]+", mensaje):
            enviar_mensaje_con_cancelar(numero, instancia, "❌ Email no válido. Por favor escribe un email correcto.\n\nEjemplo: nombre@gmail.com")
        else:
            estados[numero]["email_contacto"] = mensaje
            estados[numero]["paso"] = "pedir_iban"
            enviar_mensaje_con_cancelar(numero, instancia, "Por favor dime tu IBAN (cuenta bancaria).\n\nEjemplo: ES91 2100 0418 4502 0005 1332")

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "pedir_iban":
        iban = mensaje.replace(" ", "").upper()
        if not iban.startswith("ES") or len(iban) != 24:
            enviar_mensaje(numero, instancia, "❌ IBAN no válido. Debe empezar por ES y tener 24 caracteres.\n\nEjemplo: ES91 2100 0418 4502 0005 1332")
        else:
            estados[numero]["iban"] = mensaje
            estados[numero]["paso"] = "pedir_dni_anverso"
            enviar_mensaje_con_cancelar(numero, instancia, "Envíame una foto del DNI por el anverso (parte delantera).")
        
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
            anverso_base64 = download_response.json()["base64"]
            anverso_bytes = base64.b64decode(anverso_base64)
            
            if not es_imagen_valida_documento(anverso_bytes):
                enviar_mensaje_con_cancelar(numero, instancia, "❌ La imagen está borrosa. Por favor envía una foto más clara del DNI (anverso).")
                return
            
            ocr_response_anverso = requests.post(
                f"{OCR_URL}/ocr/extract",
                headers={"X-API-Key": OCR_API_KEY},
                files=[("images", ("dni_anverso.jpg", anverso_bytes, "image/jpeg"))],
                timeout=60
            )
            ocr_data_anverso = ocr_response_anverso.json()
            ocr_dni_anverso = ocr_data_anverso.get("dni", {})
            
            if not ocr_dni_anverso.get("numero") or ocr_data_anverso.get("tipo_documento") != "dni":
                enviar_mensaje_con_cancelar(numero, instancia, "❌ No parece ser un DNI válido. Por favor envía una foto clara del DNI (anverso).")
                return
            
            estados[numero]["dni_anverso_base64"] = anverso_base64
            estados[numero]["dni_numero_anverso"] = ocr_dni_anverso.get("numero")
            estados[numero]["paso"] = "pedir_dni_reverso"
            enviar_mensaje_con_cancelar(numero, instancia, "Perfecto. Ahora envíame una foto del DNI por el reverso (parte trasera).")
        
        except Exception as e:
            print(f"Error recibiendo DNI anverso: {e}")
            enviar_mensaje_con_cancelar(numero, instancia, "❌ No pude recibir la imagen. Por favor envíame una foto del DNI por el anverso (parte delantera).")

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "pedir_dni_reverso":
        try:
            download_url = f"{EVOLUTION_URL}/chat/getBase64FromMediaMessage/{instancia}"
            headers = {"apikey": API_KEY, "Content-Type": "application/json"}
            download_response = requests.post(
                download_url,
                json={"message": data["data"], "convertToMp4": False},
                headers=headers
            )
            reverso_base64 = download_response.json()["base64"]
            reverso_bytes = base64.b64decode(reverso_base64)
            
            if not es_imagen_valida_documento(reverso_bytes):
                enviar_mensaje_con_cancelar(numero, instancia, "❌ La imagen está borrosa. Por favor envía una foto más clara del DNI (reverso).")
                return
            
            ocr_response_reverso = requests.post(
                f"{OCR_URL}/ocr/extract",
                headers={"X-API-Key": OCR_API_KEY},
                files=[("images", ("dni_reverso.jpg", reverso_bytes, "image/jpeg"))],
                timeout=60
            )
            ocr_data_reverso = ocr_response_reverso.json()

            if ocr_data_reverso.get("tipo_documento") != "dni":
                enviar_mensaje_con_cancelar(numero, instancia, "❌ No parece ser un DNI válido. Por favor envía una foto clara del DNI (reverso).")
                return

            
            estados[numero]["dni_reverso_base64"] = reverso_base64
            dni_anverso_bytes = base64.b64decode(estados[numero]["dni_anverso_base64"])
            dni_reverso_bytes = reverso_bytes

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
                ocr_data = ocr_response.json()
                ocr_dni = ocr_data.get("dni", {})

                numero_anverso = estados[numero].get("dni_numero_anverso")
                if numero_anverso and ocr_dni.get("numero") != numero_anverso:
                    enviar_mensaje_con_cancelar(numero, instancia, "❌ Las fotos no corresponden al mismo DNI. Por favor envía ambas fotos del mismo documento.")
                    estados[numero]["paso"] = "pedir_dni_anverso"
                    return

                #verifica que no esté caducado

                fecha_exp = ocr_dni.get("fecha_expiracion")
                if fecha_exp:
                    try:
                        fecha_exp_dt = datetime.strptime(fecha_exp, "%Y-%m-%d")
                        if fecha_exp_dt < datetime.now():
                            enviar_mensaje_con_cancelar(numero, instancia, "⚠️ Tu DNI parece estar caducado. Por favor verifica que sea válido o contacta con nosotros.")
                            estados[numero]["paso"] = "pedir_dni_anverso"
                            estados[numero].pop("dni_anverso_base64", None)
                            estados[numero].pop("dni_reverso_base64", None)
                    except:
                        pass

                if not ocr_dni.get("numero") or not ocr_dni.get("nombre"):
                    enviar_mensaje_con_cancelar(numero, instancia, "❌ No he podido leer el DNI. Por favor, envía de nuevo las fotos del anverso y reverso, asegurándote de que sean claras.")
                    estados[numero]["paso"] = "pedir_dni_anverso"
                    estados[numero].pop("dni_anverso_base64", None)
                    estados[numero].pop("dni_reverso_base64", None)
                    return

            except Exception as ocr_error:
                #print(f"OCR error: {e}")
                ocr_data = {"dni": {"nombre": "", "apellidos": "", "numero": ""}}
                print(f"OCR no disponible: {ocr_error}")
                        
                    
            
            
            print(f"OCR resultado: {ocr_data}")
            
            estados[numero]["ocr_data"] = ocr_data
            estados[numero]["paso"] = "confirmar_datos"

            ocr_dni = estados[numero]["ocr_data"].get("dni", {})
            estado = estados[numero]
            tarifa_elegida = estado.get("tarifa_elegida", {})
            nombre_comercializadora = tarifa_elegida.get("comercializadora", {}).get("nombre", "")
            nombre_producto = tarifa_elegida.get("nombre", "")
            nombre_completo = f"{ocr_dni.get('nombre', '')} {ocr_dni.get('apellidos', '')}"
            numero_dni = ocr_dni.get('numero', '')

            resumen = (
                f"📋 *Resumen de tus datos:*\n\n"
                f"⚡ Tarifa elegida: *{nombre_comercializadora} - {nombre_producto}*\n"
                f"👤 Nombre: {nombre_completo}\n"
                f"🪪 DNI: {numero_dni}\n"
                f"📍 Dirección: {estado['direccion']}, {estado['poblacion']}, {estado['provincia']}\n"
                f"📞 Teléfono: {estado['telefono_contacto']}\n"
                f"📧 Email: {estado['email_contacto']}\n"
                f"🏦 IBAN: {estado['iban']}\n"
                
                f"¿Son correctos? Responde *SI* para confirmar o *NO* para cancelar."
            )
            enviar_mensaje(numero, instancia, resumen)
            
        except Exception as ocr_error:
            print(f"Error recibiendo DNI reverso: {ocr_error}")
            enviar_mensaje_con_cancelar(numero, instancia, "❌ No pude recibir la imagen. Por favor envíame una foto del DNI por el reverso (parte trasera).")
            estados[numero]["paso"] = "pedir_dni_reverso"

    elif isinstance(estado_actual, dict) and estado_actual.get("paso") == "confirmar_datos":
        if mensaje.upper() == "SI":
            try:
                e = estados[numero]
                ocr_dni = e["ocr_data"].get("dni", {})
                tarifa_elegida = e.get("tarifa_elegida", {})
                nombre_comercializadora = tarifa_elegida.get("comercializadora", {}).get("nombre", "")
                nombre_producto = tarifa_elegida.get("nombre", "")
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
    
    