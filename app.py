from flask import Flask, render_template, request, redirect, session, jsonify, url_for, flash
import mysql.connector
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import os
import requests
from dotenv import load_dotenv
from datetime import datetime, timedelta, date
from authlib.integrations.flask_client import OAuth


# =====================================================
# CONFIGURACIÓN DE VARIABLES DE ENTORNO
# =====================================================

load_dotenv()

CULQI_PUBLIC_KEY = os.getenv("CULQI_PUBLIC_KEY")
CULQI_SECRET_KEY = os.getenv("CULQI_SECRET_KEY")

# =====================================================
# CONFIGURACIÓN DE GOOGLE
# =====================================================

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")


# ============================================================
# CONFIGURACIÓN FLASK
# ============================================================

app = Flask(
    __name__,
    template_folder=os.path.join(os.getcwd(), "templates")
)

app.config["SECRET_KEY"] = os.environ.get(
    "FLASK_SECRET_KEY",
    "clave_temporal_solo_desarrollo"
)

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


# ============================================================
# INICIO DE SESIÓN CON GOOGLE
# ============================================================

oauth = OAuth(app)

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url=(
        "https://accounts.google.com/.well-known/openid-configuration"
    ),
    client_kwargs={
        "scope": "openid email profile"
    }
)


# ============================================================
# INICIO DE SESIÓN CON GOOGLE
# ============================================================

@app.route('/auth/google')
def login_google():
    redirect_uri = url_for(
        'google_callback',
        _external=True
    )

    return oauth.google.authorize_redirect(redirect_uri)


@app.route('/auth/google/callback')
def google_callback():
    conexion = None
    cursor = None

    try:
        # Obtener los datos verificados por Google
        token = oauth.google.authorize_access_token()
        datos_google = token.get('userinfo')

        if not datos_google:
            datos_google = oauth.google.userinfo(token=token)

        correo = (datos_google.get('email') or '').strip().lower()
        nombres = (datos_google.get('given_name') or '').strip()
        apellidos = (datos_google.get('family_name') or '').strip()

        if not correo or not datos_google.get('email_verified'):
            flash(
                'No se pudo verificar tu cuenta de Google.',
                'error'
            )
            return redirect(url_for('login'))

        # Conectar con la base de datos actual
        conexion = get_connection()
        cursor = conexion.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT *
            FROM usuarios
            WHERE correo = %s
            LIMIT 1
            """,
            (correo,)
        )

        usuario = cursor.fetchone()

        # Si el usuario no existe, registrarlo como cliente
        if not usuario:
            clave_aleatoria = os.urandom(32).hex()
            clave_hash = generate_password_hash(clave_aleatoria)

            cursor.execute(
                """
                INSERT INTO usuarios
                (nombres, apellidos, correo, clave, rol, telefono)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    nombres or correo.split('@')[0],
                    apellidos or '',
                    correo,
                    clave_hash,
                    'cliente',
                    ''
                )
            )

            conexion.commit()

            cursor.execute(
                """
                SELECT *
                FROM usuarios
                WHERE correo = %s
                LIMIT 1
                """,
                (correo,)
            )

            usuario = cursor.fetchone()

        if not usuario:
            raise Exception(
                'No se pudo recuperar el usuario de la base de datos.'
            )

        # Crear la sesión de la aplicación
        session.clear()
        session['usuario'] = usuario['correo']
        session['rol'] = str(usuario['rol']).strip().lower()

        # Dirigir según el rol que ya tiene en la base de datos
        destinos = {
            'admin': '/panel_admin',
            'mozo': '/panel_mozo',
            'pollero': '/panel_pollero',
            'cajero': '/panel_cajero',
            'delivery': '/panel_delivery',
            'cliente': '/carta'
        }

        destino = destinos.get(session['rol'])

        if not destino:
            session.clear()
            flash('La cuenta no tiene un rol válido.', 'error')
            return redirect(url_for('login'))

        return redirect(destino)

    except Exception as e:
        print('ERROR AL INICIAR SESIÓN CON GOOGLE:', repr(e))

        if conexion:
            try:
                conexion.rollback()
            except Exception:
                pass

        flash(
            'No se pudo iniciar sesión con Google. Inténtalo nuevamente.',
            'error'
        )
        return redirect(url_for('login'))

    finally:
        if cursor:
            cursor.close()

        if conexion:
            conexion.close()

# ============================================================
# CONFIGURACIÓN BASE DE DATOS
# ============================================================

USE_CLOUD = True


def get_connection():
    """
    Conexión principal de la aplicación.
    True  = AlwaysData
    False = MySQL/XAMPP local
    """

    if USE_CLOUD:

        return mysql.connector.connect(
            host="mysql-ehuicho.alwaysdata.net",
            user="ehuicho",
            password="MysrA6-tafzud-zezcok",
            database="ehuicho_crocantsdb",
            port=3306,
            use_pure=True
        )

    else:

        return mysql.connector.connect(
            host="localhost",
            user="root",
            password="",
            database="crocants_db",
            port=3306,
            use_pure=True
        )

# ============================================================
# STOCK COMPARTIDO DE POLLO
# ============================================================

def obtener_stock_pollo(cursor):
    """
    Obtiene el stock compartido de pollo.
    """
    cursor.execute("""
        SELECT
            pollos_enteros,
            medios_pollos,
            cuartos_pollos,
            octavos_pollos
        FROM stock_pollo
        WHERE id = 1
        FOR UPDATE
    """)

    return cursor.fetchone()

# ============================================================
# RESERVAR STOCK COMPARTIDO DE POLLO
# ============================================================

def reservar_stock_pollo(cursor, producto_id, cantidad=1):
    """
    Reserva stock de pollo de forma compartida.

    El stock interno se calcula en unidades de 1/8 de pollo.

    Equivalencias:
        1 pollo  = 8 unidades
        1/2 pollo = 4 unidades
        1/4 pollo = 2 unidades
        1/8 pollo = 1 unidad

    Retorna:
        True  = stock reservado correctamente
        False = no hay stock suficiente
    """

    # --------------------------------------------------------
    # OBTENER EQUIVALENCIA DEL PRODUCTO
    # --------------------------------------------------------

    cursor.execute("""
        SELECT equivalencia_pollo
        FROM productos
        WHERE id = %s
        FOR UPDATE
    """, (producto_id,))

    producto = cursor.fetchone()

    if not producto:
        return False

    equivalencia = producto.get('equivalencia_pollo')

    # --------------------------------------------------------
    # PRODUCTO QUE NO UTILIZA STOCK COMPARTIDO DE POLLO
    # --------------------------------------------------------

    if equivalencia is None:
        return True

    # --------------------------------------------------------
    # CANTIDAD SOLICITADA
    # --------------------------------------------------------

    try:
        cantidad = int(cantidad)
    except (TypeError, ValueError):
        return False

    if cantidad <= 0:
        return False

    # --------------------------------------------------------
    # EQUIVALENCIA EN OCTAVOS DE POLLO
    # --------------------------------------------------------

    reglas = {
        8: 8,   # Pollo entero
        4: 4,   # 1/2 pollo
        2: 2,   # 1/4 pollo
        1: 1    # 1/8 pollo
    }

    if equivalencia not in reglas:
        return False

    unidades_a_descontar = (
        reglas[equivalencia] * cantidad
    )

    # --------------------------------------------------------
    # OBTENER STOCK ACTUAL
    # --------------------------------------------------------

    cursor.execute("""
        SELECT
            pollos_enteros,
            medios_pollos,
            cuartos_pollos,
            octavos_pollos
        FROM stock_pollo
        WHERE id = 1
        FOR UPDATE
    """)

    stock = cursor.fetchone()

    if not stock:
        return False

    # --------------------------------------------------------
    # CONVERTIR TODO EL STOCK ACTUAL A OCTAVOS
    # --------------------------------------------------------

    unidades_disponibles = (
        (stock['pollos_enteros'] or 0) * 8
        +
        (stock['medios_pollos'] or 0) * 4
        +
        (stock['cuartos_pollos'] or 0) * 2
        +
        (stock['octavos_pollos'] or 0)
    )

    # --------------------------------------------------------
    # COMPROBAR STOCK SUFICIENTE
    # --------------------------------------------------------

    if unidades_disponibles < unidades_a_descontar:
        return False

    # --------------------------------------------------------
    # DESCONTAR DEL STOCK
    #
    # Convertimos el stock completo a octavos y luego
    # reconstruimos enteros, medios, cuartos y octavos.
    # --------------------------------------------------------

    nuevo_stock = (
        unidades_disponibles
        - unidades_a_descontar
    )

    nuevos_enteros = nuevo_stock // 8
    resto = nuevo_stock % 8

    nuevos_medios = resto // 4
    resto = resto % 4

    nuevos_cuartos = resto // 2
    nuevos_octavos = resto % 2

    # --------------------------------------------------------
    # ACTUALIZAR STOCK
    # --------------------------------------------------------

    cursor.execute("""
        UPDATE stock_pollo
        SET
            pollos_enteros = %s,
            medios_pollos = %s,
            cuartos_pollos = %s,
            octavos_pollos = %s
        WHERE id = 1
    """, (
        nuevos_enteros,
        nuevos_medios,
        nuevos_cuartos,
        nuevos_octavos
    ))

    return True

# ============================================================
# PRUEBA DE CONEXIÓN
# ============================================================

try:

    conn = get_connection()

    if USE_CLOUD:
        print("✅ CONECTADO A ALWAYS DATA")
    else:
        print("✅ CONECTADO A MYSQL LOCAL")

    conn.close()

except Exception as e:

    print("❌ ERROR DE CONEXIÓN:")
    print(e)


# ============================================================
# PRUEBA WEB DE BASE DE DATOS
# ============================================================

@app.route('/test_db')
def test_db():

    conexion = None

    try:

        conexion = get_connection()

        cursor = conexion.cursor()

        cursor.execute("SELECT DATABASE()")

        db = cursor.fetchone()

        cursor.close()

        return f"✅ Conectado a BD: {db}"

    except Exception as e:

        return f"❌ Error: {e}"

    finally:

        if conexion:
            conexion.close()


# ============================================================
# CREAR ADMIN
# ============================================================

def crear_admin():

    conexion = None
    cursor = None

    try:

        conexion = get_connection()

        cursor = conexion.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT *
            FROM usuarios
            WHERE correo=%s
            """,
            ('admin@mail.com',)
        )

        existe = cursor.fetchone()

        if not existe:

            clave = generate_password_hash('1234')

            cursor.execute(
                """
                INSERT INTO usuarios
                (
                    correo,
                    clave,
                    rol
                )
                VALUES
                (%s,%s,%s)
                """,
                (
                    'admin@mail.com',
                    clave,
                    'admin'
                )
            )

            conexion.commit()

            print("✅ ADMIN CREADO")

    except Exception as e:

        print("❌ ERROR CREANDO ADMIN:")
        print(e)

    finally:

        if cursor:
            cursor.close()

        if conexion:
            conexion.close()


crear_admin()


# ============================================================
# INICIO
# ============================================================

@app.route('/')
def index():
    return render_template('index.html')


# ============================================================
# CONTACTO
# ============================================================

@app.route('/contacto')
def contacto():
    return render_template('contacto.html')

  # ============================================================
# LOGIN
# ============================================================

@app.route('/login', methods=['GET', 'POST'])
def login():

    if request.method == 'POST':

        correo = request.form.get('correo', '').strip()
        clave = request.form.get('clave', '')
        ip = request.remote_addr

        # ====================================================
        # VERIFICAR BLOQUEO
        # ====================================================

        if esta_bloqueado(correo, ip):

            conexion = None
            cursor = None
            bloqueo = None

            try:
                conexion = get_connection()
                cursor = conexion.cursor(dictionary=True)

                cursor.execute("""
                    SELECT *
                    FROM bloqueos
                    WHERE ip = %s
                    ORDER BY id DESC
                    LIMIT 1
                """, (ip,))

                bloqueo = cursor.fetchone()

            finally:
                if cursor:
                    cursor.close()
                if conexion:
                    conexion.close()

            if bloqueo and bloqueo.get('correo') == correo:
                return render_template(
                    'login.html',
                    error='Usuario bloqueado por demasiados intentos. Intenta nuevamente en 15 minutos.'
                )

            if not bloqueo or bloqueo.get('intentos', 0) >= 6:
                return render_template(
                    'login.html',
                    error='La dirección IP ha sido bloqueada por demasiados intentos. Intenta nuevamente en 15 minutos.'
                )

        # ====================================================
        # BUSCAR USUARIO
        # ====================================================

        conexion = None
        cursor = None
        usuario = None

        try:
            conexion = get_connection()
            cursor = conexion.cursor(dictionary=True)

            cursor.execute("""
                SELECT *
                FROM usuarios
                WHERE correo = %s
                LIMIT 1
            """, (correo,))

            usuario = cursor.fetchone()

        finally:
            if cursor:
                cursor.close()
            if conexion:
                conexion.close()

        # ====================================================
        # VERIFICAR USUARIO Y CONTRASEÑA
        # ====================================================

        if usuario:

            contraseña_correcta = False

            try:
                contraseña_correcta = check_password_hash(
                    usuario['clave'],
                    clave
                )
            except (TypeError, ValueError):
                contraseña_correcta = False

            # Compatibilidad con contraseñas antiguas en texto plano
            if not contraseña_correcta and clave == usuario['clave']:

                nueva_hash = generate_password_hash(clave)
                conexion = None
                cursor = None

                try:
                    conexion = get_connection()
                    cursor = conexion.cursor()

                    cursor.execute("""
                        UPDATE usuarios
                        SET clave = %s
                        WHERE correo = %s
                    """, (nueva_hash, usuario['correo']))

                    conexion.commit()
                    contraseña_correcta = True

                except Exception:
                    if conexion:
                        conexion.rollback()
                    app.logger.exception(
                        'Error actualizando contraseña antigua'
                    )
                    contraseña_correcta = False

                finally:
                    if cursor:
                        cursor.close()
                    if conexion:
                        conexion.close()

            # =================================================
            # LOGIN CORRECTO
            # =================================================

            if contraseña_correcta:

                limpiar_bloqueo(correo, ip)

                # Eliminar los datos de autenticación anteriores
                session.clear()

                # Guardar la identidad de la cuenta autenticada
                session['usuario'] = usuario['correo']
                session['rol'] = (
                    str(usuario.get('rol') or '')
                    .strip()
                    .lower()
                )

                print('LOGIN CORRECTO:', session['usuario'])
                print('ROL FINAL:', session['rol'])

                # =============================================
                # REDIRECCIÓN SEGÚN ROL
                # =============================================

                destinos = {
                    'admin': '/panel_admin',
                    'mozo': '/panel_mozo',
                    'pollero': '/panel_pollero',
                    'cajero': '/panel_cajero',
                    'delivery': '/panel_delivery',
                    'cliente': '/carta'
                }

                destino = destinos.get(session['rol'])

                if destino:
                    return redirect(destino)

                session.clear()

                return render_template(
                    'login.html',
                    error='El usuario no tiene un rol válido.'
                )

        # ====================================================
        # LOGIN INCORRECTO
        # ====================================================

        registrar_intento_fallido(correo, ip)

        return render_template(
            'login.html',
            error='Credenciales incorrectas.'
        )

    return render_template('login.html')


# ============================================================
# CARTA - TODOS
# SOLO USUARIO LOGUEADO
# ============================================================

@app.route('/carta')
def carta():

    # --------------------------------------------------------
    # VERIFICAR SESIÓN
    # --------------------------------------------------------

    if 'usuario' not in session:
        return redirect('/login')

    conexion = get_connection()
    cursor = conexion.cursor(dictionary=True)

    try:

        # ----------------------------------------------------
        # OBTENER PRODUCTOS
        # ----------------------------------------------------

        cursor.execute("""
            SELECT *
            FROM productos
            WHERE LOWER(
                REPLACE(categoria, 'ó', 'o')
            ) IN (
                'pollos',
                'bebidas',
                'postres',
                'cocteles',
                'complementos'
            )
            ORDER BY
                CASE
                    WHEN LOWER(REPLACE(categoria, 'ó', 'o')) = 'pollos' THEN 1
                    WHEN LOWER(REPLACE(categoria, 'ó', 'o')) = 'bebidas' THEN 2
                    WHEN LOWER(REPLACE(categoria, 'ó', 'o')) = 'postres' THEN 3
                    WHEN LOWER(REPLACE(categoria, 'ó', 'o')) = 'cocteles' THEN 4
                    WHEN LOWER(REPLACE(categoria, 'ó', 'o')) = 'complementos' THEN 5
                    ELSE 6
                END,
                id ASC
        """)

        productos = cursor.fetchall()

        # ----------------------------------------------------
        # OBTENER STOCK CENTRAL DEL POLLO
        # ----------------------------------------------------

        cursor.execute("""
            SELECT
                pollos_enteros,
                medios_pollos,
                cuartos_pollos,
                octavos_pollos
            FROM stock_pollo
            WHERE id = 1
            LIMIT 1
        """)

        stock_pollo = cursor.fetchone()

        if not stock_pollo:

            stock_pollo = {
                'pollos_enteros': 0,
                'medios_pollos': 0,
                'cuartos_pollos': 0,
                'octavos_pollos': 0
            }

        # ----------------------------------------------------
        # CONVERTIR TODO EL STOCK A OCTAVOS
        #
        # 1 entero = 8 octavos
        # 1/2      = 4 octavos
        # 1/4      = 2 octavos
        # 1/8      = 1 octavo
        # ----------------------------------------------------

        total_octavos = (
            int(stock_pollo.get('pollos_enteros') or 0) * 8
            +
            int(stock_pollo.get('medios_pollos') or 0) * 4
            +
            int(stock_pollo.get('cuartos_pollos') or 0) * 2
            +
            int(stock_pollo.get('octavos_pollos') or 0)
        )

        # ----------------------------------------------------
        # STOCK DISPONIBLE POR PRESENTACIÓN
        #
        # Ejemplo con 160 octavos:
        #
        # Enteros = 160 // 8 = 20
        # Medios   = 160 // 4 = 40
        # Cuartos  = 160 // 2 = 80
        # Octavos  = 160
        # ----------------------------------------------------

        stock_enteros = total_octavos // 8
        stock_medios = total_octavos // 4
        stock_cuartos = total_octavos // 2
        stock_octavos = total_octavos

        # ----------------------------------------------------
        # ASIGNAR STOCK A CADA PRODUCTO
        # ----------------------------------------------------

        for producto in productos:

            equivalencia = producto.get(
                'equivalencia_pollo'
            )

            if equivalencia is not None:

                try:

                    equivalencia = int(
                        equivalencia
                    )

                except (
                    TypeError,
                    ValueError
                ):

                    equivalencia = None

            # ------------------------------------------------
            # POLLO ENTERO
            # ------------------------------------------------

            if equivalencia == 8:

                producto['stock_mostrar'] = (
                    stock_enteros
                )

            # ------------------------------------------------
            # 1/2 POLLO
            # ------------------------------------------------

            elif equivalencia == 4:

                producto['stock_mostrar'] = (
                    stock_medios
                )

            # ------------------------------------------------
            # 1/4 POLLO
            # ------------------------------------------------

            elif equivalencia == 2:

                producto['stock_mostrar'] = (
                    stock_cuartos
                )

            # ------------------------------------------------
            # 1/8 POLLO
            # ------------------------------------------------

            elif equivalencia == 1:

                producto['stock_mostrar'] = (
                    stock_octavos
                )

            # ------------------------------------------------
            # PRODUCTO NORMAL
            # ------------------------------------------------

            else:

                producto['stock_mostrar'] = int(
                    producto.get('stock') or 0
                )

    finally:

        cursor.close()
        conexion.close()

    # --------------------------------------------------------
    # MOSTRAR CARTA DEL CLIENTE
    # --------------------------------------------------------

    return render_template(
        'carta.html',
        productos=productos,
        categoria_actual='Todos',
        modo='cliente'
    )


# ============================================================
# CARTA - CATEGORÍAS
# SOLO USUARIO LOGUEADO
# ============================================================

@app.route('/carta/<categoria>')
def carta_categoria(categoria):

    # --------------------------------------------------------
    # VERIFICAR SESIÓN
    # --------------------------------------------------------

    if 'usuario' not in session:
        return redirect('/login')

    # --------------------------------------------------------
    # CATEGORÍAS VÁLIDAS
    # --------------------------------------------------------

    categorias_validas = [
        'pollos',
        'bebidas',
        'postres',
        'cocteles',
        'complementos'
    ]

    categoria = categoria.lower().strip()

    if categoria not in categorias_validas:
        return redirect('/carta')

    # --------------------------------------------------------
    # CONEXIÓN
    # --------------------------------------------------------

    conexion = get_connection()
    cursor = conexion.cursor(dictionary=True)

    try:

        # ----------------------------------------------------
        # OBTENER PRODUCTOS
        # ----------------------------------------------------

        cursor.execute("""
            SELECT *
            FROM productos
            WHERE LOWER(
                REPLACE(categoria, 'ó', 'o')
            ) = %s
            ORDER BY id ASC
        """, (categoria,))

        productos = cursor.fetchall()

        # ----------------------------------------------------
        # OBTENER STOCK CENTRAL DEL POLLO
        # ----------------------------------------------------

        cursor.execute("""
            SELECT
                pollos_enteros,
                medios_pollos,
                cuartos_pollos,
                octavos_pollos
            FROM stock_pollo
            WHERE id = 1
            LIMIT 1
        """)

        stock_pollo = cursor.fetchone()

        if not stock_pollo:

            stock_pollo = {
                'pollos_enteros': 0,
                'medios_pollos': 0,
                'cuartos_pollos': 0,
                'octavos_pollos': 0
            }

        # ----------------------------------------------------
        # CONVERTIR TODO A OCTAVOS
        # ----------------------------------------------------

        total_octavos = (
            int(stock_pollo.get('pollos_enteros') or 0) * 8
            +
            int(stock_pollo.get('medios_pollos') or 0) * 4
            +
            int(stock_pollo.get('cuartos_pollos') or 0) * 2
            +
            int(stock_pollo.get('octavos_pollos') or 0)
        )

        # ----------------------------------------------------
        # STOCK POR PRESENTACIÓN
        # ----------------------------------------------------

        stock_enteros = total_octavos // 8
        stock_medios = total_octavos // 4
        stock_cuartos = total_octavos // 2
        stock_octavos = total_octavos

        # ----------------------------------------------------
        # ASIGNAR STOCK A CADA PRODUCTO
        # ----------------------------------------------------

        for producto in productos:

            equivalencia = producto.get(
                'equivalencia_pollo'
            )

            if equivalencia is not None:

                try:

                    equivalencia = int(
                        equivalencia
                    )

                except (
                    TypeError,
                    ValueError
                ):

                    equivalencia = None

            # ------------------------------------------------
            # POLLO ENTERO
            # ------------------------------------------------

            if equivalencia == 8:

                producto['stock_mostrar'] = (
                    stock_enteros
                )

            # ------------------------------------------------
            # 1/2 POLLO
            # ------------------------------------------------

            elif equivalencia == 4:

                producto['stock_mostrar'] = (
                    stock_medios
                )

            # ------------------------------------------------
            # 1/4 POLLO
            # ------------------------------------------------

            elif equivalencia == 2:

                producto['stock_mostrar'] = (
                    stock_cuartos
                )

            # ------------------------------------------------
            # 1/8 POLLO
            # ------------------------------------------------

            elif equivalencia == 1:

                producto['stock_mostrar'] = (
                    stock_octavos
                )

            # ------------------------------------------------
            # PRODUCTO NORMAL
            # ------------------------------------------------

            else:

                producto['stock_mostrar'] = int(
                    producto.get('stock') or 0
                )

    finally:

        cursor.close()
        conexion.close()

    # --------------------------------------------------------
    # NOMBRES PARA MOSTRAR
    # --------------------------------------------------------

    nombres = {

        'pollos': 'Pollos',

        'bebidas': 'Bebidas',

        'postres': 'Postres',

        'cocteles': 'Cócteles',

        'complementos': 'Complementos'
    }

    # --------------------------------------------------------
    # MOSTRAR CARTA DEL CLIENTE
    # --------------------------------------------------------

    return render_template(
        'carta.html',
        productos=productos,
        categoria_actual=nombres[categoria],
        modo='cliente'
    )


# ============================================================
# MENÚ PÚBLICO
# SIN LOGIN
# USA EL MISMO carta.html
# ============================================================

@app.route('/menu')
def menu():

    conexion = get_connection()
    cursor = conexion.cursor(dictionary=True)

    try:

        categoria = request.args.get('categoria')

        # ----------------------------------------------------
        # PRODUCTOS POR CATEGORÍA
        # ----------------------------------------------------

        if categoria:

            categoria = categoria.lower().strip()

            cursor.execute("""
                SELECT *
                FROM productos
                WHERE LOWER(
                    REPLACE(categoria, 'ó', 'o')
                ) = %s
                AND LOWER(
                    REPLACE(categoria, 'ó', 'o')
                ) != 'complementos'
                ORDER BY id ASC
            """, (categoria,))

        else:

            cursor.execute("""
                SELECT *
                FROM productos
                WHERE LOWER(
                    REPLACE(categoria, 'ó', 'o')
                ) IN (
                    'pollos',
                    'bebidas',
                    'postres',
                    'cocteles'
                )
                ORDER BY
                    CASE
                        WHEN LOWER(REPLACE(categoria, 'ó', 'o')) = 'pollos' THEN 1
                        WHEN LOWER(REPLACE(categoria, 'ó', 'o')) = 'bebidas' THEN 2
                        WHEN LOWER(REPLACE(categoria, 'ó', 'o')) = 'postres' THEN 3
                        WHEN LOWER(REPLACE(categoria, 'ó', 'o')) = 'cocteles' THEN 4
                        ELSE 5
                    END,
                    id ASC
            """)

        productos = cursor.fetchall()

    finally:

        cursor.close()
        conexion.close()

    # --------------------------------------------------------
    # NOMBRES DE CATEGORÍAS
    # --------------------------------------------------------

    nombres = {
        'pollos': 'Pollos',
        'bebidas': 'Bebidas',
        'postres': 'Postres',
        'cocteles': 'Cócteles'
    }

    # --------------------------------------------------------
    # MOSTRAR MENÚ PÚBLICO
    # MISMO carta.html
    # PERO EN MODO PUBLICO
    # --------------------------------------------------------

    return render_template(
        'carta.html',
        productos=productos,
        categoria_actual=nombres.get(categoria, 'Todos'),
        modo='publico'
    )

# ============================================================
# PROCESAR PAGO CULQI
# ============================================================

@app.route('/culqi/procesar-pago', methods=['POST'])
def procesar_pago_culqi():

    if 'usuario' not in session:
        return jsonify({
            'ok': False,
            'mensaje': 'La sesión ha expirado.'
        }), 401

    try:

        # ========================================================
        # RECIBIR DATOS DEL FRONTEND
        # ========================================================

        datos = request.get_json(
            silent=True
        ) or {}

        token_id = str(
            datos.get('token_id', '') or ''
        ).strip()

        monto = datos.get('monto')

        # ========================================================
        # MÉTODO ENVIADO POR EL FRONTEND
        #
        # Puede venir como:
        #
        # Yape
        # Tarjeta
        #
        # Este es el medio utilizado DENTRO DE CULQI.
        # ========================================================

        metodo_enviado = str(
            datos.get('metodo_culqi', '') or ''
        ).strip()

        # ========================================================
        # VALIDAR TOKEN
        # ========================================================

        if not token_id:

            return jsonify({
                'ok': False,
                'mensaje':
                    'No se recibió el token de Culqi.'
            }), 400

        # ========================================================
        # IDENTIFICAR MEDIO DE PAGO DENTRO DE CULQI
        #
        # Yape:
        # ype_...
        #
        # Tarjeta:
        # tkn_...
        # ========================================================

        token_normalizado = token_id.lower()

        if token_normalizado.startswith('ype_'):

            metodo_culqi = 'Yape'

        elif token_normalizado.startswith('tkn_'):

            metodo_culqi = 'Tarjeta'

        else:

            # ----------------------------------------------------
            # RESPALDO POR SI EL FRONTEND ENVÍA EL MÉTODO
            # ----------------------------------------------------

            metodo_normalizado = (
                metodo_enviado
                .lower()
                .strip()
            )

            if metodo_normalizado == 'yape':

                metodo_culqi = 'Yape'

            elif metodo_normalizado == 'tarjeta':

                metodo_culqi = 'Tarjeta'

            else:

                return jsonify({
                    'ok': False,
                    'mensaje':
                        'No se pudo identificar si el pago '
                        'fue realizado con Yape o Tarjeta.'
                }), 400

        # ========================================================
        # VALIDAR MONTO
        # ========================================================

        try:

            monto = float(monto)

        except (
            TypeError,
            ValueError
        ):

            return jsonify({
                'ok': False,
                'mensaje':
                    'El monto no es válido.'
            }), 400

        if monto <= 0:

            return jsonify({
                'ok': False,
                'mensaje':
                    'El monto debe ser mayor a cero.'
            }), 400

        monto_centimos = int(
            round(
                monto * 100
            )
        )

        # ========================================================
        # EMAIL
        # ========================================================

        email = (
            session.get('usuario_email')
            or session.get('usuario')
            or 'cliente@crocants.com'
        )

        # ========================================================
        # VERIFICAR CONFIGURACIÓN CULQI
        # ========================================================

        if not CULQI_SECRET_KEY:

            print(
                "❌ CULQI_SECRET_KEY no está configurada."
            )

            return jsonify({
                'ok': False,
                'mensaje':
                    'La configuración de Culqi '
                    'está incompleta.'
            }), 500

        # ========================================================
        # HEADERS
        # ========================================================

        headers = {

            'Authorization':
                f'Bearer {CULQI_SECRET_KEY}',

            'Content-Type':
                'application/json'
        }

        # ========================================================
        # DATOS DEL COBRO
        # ========================================================

        payload = {

            'amount':
                monto_centimos,

            'currency_code':
                'PEN',

            'email':
                email,

            'source_id':
                token_id,

            'capture':
                True,

            'description':
                "Pedido Crocant's"
        }

        # ========================================================
        # ENVIAR COBRO A CULQI
        # ========================================================

        respuesta = requests.post(
            'https://api.culqi.com/v2/charges',
            headers=headers,
            json=payload,
            timeout=30
        )

        # ========================================================
        # LEER RESPUESTA
        # ========================================================

        try:

            resultado = respuesta.json()

        except ValueError:

            resultado = {}

        # ========================================================
        # MOSTRAR INFORMACIÓN EN CONSOLA
        # ========================================================

        print(
            "============================================"
        )

        print(
            "💳 RESPUESTA CULQI"
        )

        print(
            "HTTP:",
            respuesta.status_code
        )

        print(
            "MÉTODO PRINCIPAL:",
            "Culqi"
        )

        print(
            "MEDIO DENTRO DE CULQI:",
            metodo_culqi
        )

        print(
            "TOKEN:",
            token_id[:10] + "..."
        )

        print(
            "RESULTADO:",
            resultado
        )

        print(
            "============================================"
        )

        # ========================================================
        # PAGO APROBADO
        # ========================================================

        if respuesta.ok:

            # ----------------------------------------------------
            # GUARDAR EL MEDIO REAL EN LA SESIÓN
            #
            # Esto será utilizado posteriormente por:
            #
            # /finalizar_pedido
            #
            # para guardar:
            #
            # metodo_pago = Culqi
            # tipo_pago_culqi = Yape / Tarjeta
            # ----------------------------------------------------

            session['culqi_metodo'] = metodo_culqi

            # ----------------------------------------------------
            # GUARDAR EL ID DEL COBRO
            # ----------------------------------------------------

            session['culqi_charge_id'] = (
                resultado.get('id')
            )

            session.modified = True

            # ----------------------------------------------------
            # RESPUESTA AL FRONTEND
            #
            # Culqi = MÉTODO PRINCIPAL
            # Yape/Tarjeta = MEDIO DENTRO DE CULQI
            # ----------------------------------------------------

            return jsonify({

                'ok':
                    True,

                'mensaje':
                    'Pago aprobado correctamente.',

                'charge_id':
                    resultado.get('id'),

                'metodo_pago':
                    'Culqi',

                'metodo_culqi':
                    metodo_culqi
            })

        # ========================================================
        # PAGO RECHAZADO
        # ========================================================

        mensaje = (

            resultado.get(
                'user_message'
            )

            or resultado.get(
                'merchant_message'
            )

            or resultado.get(
                'message'
            )

            or 'Culqi rechazó el pago.'
        )

        return jsonify({

            'ok':
                False,

            'mensaje':
                mensaje

        }), 400

    # ============================================================
    # ERROR DE CONEXIÓN
    # ============================================================

    except requests.exceptions.RequestException as e:

        print(
            "❌ ERROR DE CONEXIÓN CON CULQI:",
            str(e)
        )

        return jsonify({

            'ok':
                False,

            'mensaje':
                'No se pudo conectar con Culqi.'

        }), 502

    # ============================================================
    # ERROR GENERAL
    # ============================================================

    except Exception as e:

        print(
            "❌ ERROR PROCESANDO CULQI:",
            type(e).__name__,
            str(e)
        )

        return jsonify({

            'ok':
                False,

            'mensaje':
                'Ocurrió un error al procesar el pago.'

        }), 500
    
# ============================================================
# FINALIZAR PEDIDO
# ============================================================
@app.route('/finalizar_pedido', methods=['POST'])
def finalizar_pedido():
    # ========================================================
    # VERIFICAR SESIÓN
    # ========================================================
    if 'usuario' not in session:
        return redirect(url_for('login'))
    # ========================================================
    # CARRITO Y DATOS DEL PEDIDO
    # ========================================================
    carrito = session.get('carrito', [])
    datos = session.get('pedido', {})
    if not carrito:
        return redirect('/carta')
    # ========================================================
    # MÉTODO DE PAGO
    # ========================================================
    metodo = request.form.get('metodo')
    if not metodo:
        return "Selecciona un método de pago ❌"
    metodo = metodo.strip()
    # ========================================================
    # VARIABLES DE PAGO
    # ========================================================
    monto_efectivo = None
    vuelto = None
    culqi_token = None
    culqi_payment_id = request.form.get(
        'culqi_payment_id'
    )
    culqi_order_id = request.form.get(
        'culqi_order_id'
    )
    # ========================================================
    # TIPO DE PAGO DENTRO DE CULQI
    # ========================================================
    tipo_pago_culqi = session.get(
        'culqi_metodo'
    )
    if tipo_pago_culqi:
        tipo_pago_culqi = str(
            tipo_pago_culqi
        ).strip()
        if tipo_pago_culqi.lower() == 'yape':
            tipo_pago_culqi = 'Yape'
        elif tipo_pago_culqi.lower() == 'tarjeta':
            tipo_pago_culqi = 'Tarjeta'
        else:
            tipo_pago_culqi = None
    # ========================================================
    # MÉTODO FINAL QUE SE GUARDARÁ EN LA BD
    # ========================================================
    metodo_pago_final = None
    # ========================================================
    # PAGO CONTRA ENTREGA
    # ========================================================
    if metodo.lower() == 'contra entrega':
        metodo_pago_final = 'Efectivo'
        tipo_pago_culqi = None
        monto_efectivo = request.form.get(
            'monto_efectivo'
        )
        if not monto_efectivo:
            return (
                "Ingresa con cuánto pagarás ❌"
            )
        try:
            monto_efectivo = round(
                float(monto_efectivo),
                2
            )
        except (
            TypeError,
            ValueError
        ):
            return (
                "El monto en efectivo "
                "no es válido ❌"
            )
        if monto_efectivo <= 0:
            return (
                "El monto en efectivo "
                "debe ser mayor a 0 ❌"
            )
    # ========================================================
    # PAGO CULQI
    # ========================================================
    elif metodo.lower() == 'culqi':
        metodo_pago_final = 'Culqi'
        culqi_token = request.form.get(
            'culqi_token'
        )
        if not culqi_token:
            return """
            <div style="
                max-width:700px;
                margin:80px auto;
                padding:30px;
                font-family:Arial,sans-serif;
                text-align:center;
            ">
                <h2 style="color:#f58220;">
                    Pago Culqi pendiente
                </h2>
                <p>
                    Debes completar el pago mediante Culqi
                    antes de confirmar el pedido.
                </p>
                <a
                    href="/pago"
                    style="
                        display:inline-block;
                        margin-top:20px;
                        padding:12px 22px;
                        background:#f58220;
                        color:white;
                        text-decoration:none;
                        border-radius:8px;
                        font-weight:bold;
                    "
                >
                    Volver al pago
                </a>
            </div>
            """
        # ====================================================
        # OBTENER MEDIO REAL DE CULQI
        # ====================================================
        if tipo_pago_culqi not in (
            'Yape',
            'Tarjeta'
        ):
            token_texto = str(
                culqi_token
            ).strip().lower()
            if token_texto.startswith(
                'ype_'
            ):
                tipo_pago_culqi = 'Yape'
            elif token_texto.startswith(
                'tkn_'
            ):
                tipo_pago_culqi = 'Tarjeta'
            else:
                return (
                    "No se pudo identificar "
                    "si el pago Culqi fue realizado "
                    "con Yape o Tarjeta ❌"
                )
    # ========================================================
    # MÉTODO NO VÁLIDO
    # ========================================================
    else:
        return (
            "El método de pago seleccionado "
            "no es válido ❌"
        )
    # ========================================================
    # CALCULAR TOTAL
    # ========================================================
    total = 0.00
    for item in carrito:
        try:
            precio = float(
                item.get(
                    'precio',
                    0
                )
            )
            cantidad = int(
                item.get(
                    'cantidad',
                    1
                )
            )
        except (
            TypeError,
            ValueError
        ):
            return (
                "Los datos del carrito "
                "no son válidos ❌"
            )
        if cantidad <= 0:
            return (
                "La cantidad de un producto "
                "no es válida ❌"
            )
        total += precio * cantidad
    total = round(
        total,
        2
    )
    # ========================================================
    # CALCULAR VUELTO
    # ========================================================
    if metodo_pago_final == 'Efectivo':
        if monto_efectivo < total:
            return (
                f"El monto ingresado "
                f"(S/ {monto_efectivo:.2f}) "
                f"es menor al total "
                f"(S/ {total:.2f}) ❌"
            )
        vuelto = round(
            monto_efectivo - total,
            2
        )
    else:
        monto_efectivo = None
        vuelto = 0.00
    # ========================================================
    # VARIABLES DE CONEXIÓN
    # ========================================================
    conexion = None
    cursor = None
    try:
        # ====================================================
        # CONEXIÓN
        # ====================================================
        conexion = get_connection()
        cursor = conexion.cursor(
            dictionary=True
        )
        # ====================================================
        # OBTENER DATOS DEL USUARIO
        # ====================================================
        cursor.execute(
            """
            SELECT
                id,
                nombres,
                correo
            FROM usuarios
            WHERE correo = %s
            LIMIT 1
            """,
            (
                session['usuario'],
            )
        )
        usuario_db = cursor.fetchone()
        # ====================================================
        # NOMBRE DEL CLIENTE
        # ====================================================
        if (
            usuario_db
            and usuario_db.get('nombres')
        ):
            nombre_cliente = (
                usuario_db['nombres']
            )
        else:
            nombre_cliente = 'Cliente'
        if datos.get('nombre'):
            nombre_cliente = str(
                datos.get('nombre')
            ).strip()
        if not nombre_cliente:
            nombre_cliente = 'Cliente'
        # ====================================================
        # DATOS ADICIONALES
        # ====================================================
        telefono = datos.get(
            'telefono'
        )
        direccion = datos.get(
            'direccion'
        )
        referencia = datos.get(
            'referencia'
        )
        # ====================================================
        # TIPO DE DOCUMENTO
        # ====================================================
        tipo_documento = datos.get(
            'tipo'
        )
        documento = datos.get(
            'documento'
        )
        if tipo_documento:
            tipo_documento = str(
                tipo_documento
            ).strip()
        if documento:
            documento = str(
                documento
            ).strip()
        if not tipo_documento:
            return (
                "Selecciona un tipo "
                "de documento ❌"
            )
        if not documento:
            return (
                "Ingresa tu número "
                "de documento ❌"
            )
        # ====================================================
        # BUSCAR TIPO DE DOCUMENTO
        # ====================================================
        cursor.execute(
            """
            SELECT
                id,
                nombre
            FROM tipos_documento
            WHERE LOWER(TRIM(nombre))
                = LOWER(TRIM(%s))
            AND activo = 1
            LIMIT 1
            """,
            (
                tipo_documento,
            )
        )
        tipo_documento_db = (
            cursor.fetchone()
        )
        if not tipo_documento_db:
            return (
                "El tipo de documento "
                "seleccionado no es válido ❌"
            )
        tipo_documento_id = (
            tipo_documento_db['id']
        )
        # ====================================================
        # UBICACIÓN
        # ====================================================
        latitud = datos.get(
            'latitud'
        )
        longitud = datos.get(
            'longitud'
        )
        # ====================================================
        # TIPO DE PEDIDO
        # ====================================================
        tipo_pedido = datos.get(
            'tipo_pedido'
        )
        if not tipo_pedido:
            tipo_pedido = 'Delivery'
        tipo_pedido = str(
            tipo_pedido
        ).strip()
        # ====================================================
        # MESA
        # ====================================================
        mesa_id = datos.get(
            'mesa'
        )
        if mesa_id in (
            '',
            None
        ):
            mesa_id = None
        else:
            try:
                mesa_id = int(
                    mesa_id
                )
            except (
                TypeError,
                ValueError
            ):
                mesa_id = None
        # ====================================================
        # OBSERVACIÓN
        # ====================================================
        observacion = (
            datos.get('observacion')
            or datos.get('detalle')
            or ''
        )
        # ====================================================
        # INFORMACIÓN DE EFECTIVO
        # ====================================================
        if metodo_pago_final == 'Efectivo':
            informacion_efectivo = (
                f"Pago contra entrega. "
                f"Cliente paga S/ "
                f"{monto_efectivo:.2f}. "
                f"Total S/ "
                f"{total:.2f}. "
                f"Vuelto S/ "
                f"{vuelto:.2f}."
            )
            if observacion:
                observacion = (
                    f"{observacion} | "
                    f"{informacion_efectivo}"
                )
            else:
                observacion = (
                    informacion_efectivo
                )
        # ====================================================
        # INSERTAR PEDIDO PRINCIPAL
        # ====================================================
        cursor.execute(
            """
            INSERT INTO pedidos
            (
                usuario_id,
                cliente,
                telefono,
                direccion,
                referencia,
                metodo_pago,
                tipo_pago_culqi,
                total,
                estado,
                cliente_email,
                tipo_pedido,
                mesa_id,
                latitud,
                longitud,
                tipo_documento_id,
                documento
            )
            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s
            )
            """,
            (
                usuario_db['id']
                if usuario_db
                else None,
                nombre_cliente,
                telefono,
                direccion,
                referencia,
                metodo_pago_final,
                tipo_pago_culqi,
                total,
                'Recibido',
                session['usuario'],
                tipo_pedido,
                mesa_id,
                latitud,
                longitud,
                tipo_documento_id,
                documento
            )
        )
        # ====================================================
        # OBTENER ID DEL PEDIDO
        # ====================================================
        pedido_id = (
            cursor.lastrowid
        )
        # ====================================================
        # SINCRONIZAR DELIVERY
        # ====================================================
        if (
            str(tipo_pedido)
            .strip()
            .lower()
            == 'delivery'
        ):
            cursor.execute(
                """
                INSERT INTO delivery
                (
                    pedido_id,
                    trabajador_id,
                    estado,
                    observacion
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    pedido_id,
                    None,
                    'Pendiente',
                    observacion
                )
            )
        # ====================================================
        # PROCESAR CADA PRODUCTO
        # ====================================================
        for item in carrito:
            try:
                producto_id = int(
                    item.get('id')
                )
                cantidad = int(
                    item.get(
                        'cantidad',
                        1
                    )
                )
                precio_carrito = float(
                    item.get(
                        'precio',
                        0
                    )
                )
            except (
                TypeError,
                ValueError
            ):
                raise Exception(
                    'Los datos de un producto '
                    'no son válidos.'
                )
            if cantidad <= 0:
                raise Exception(
                    'La cantidad del producto '
                    'no es válida.'
                )
            # =================================================
            # OBTENER PRODUCTO
            # =================================================
            cursor.execute(
                """
                SELECT
                    id,
                    nombre,
                    precio,
                    categoria,
                    stock,
                    equivalencia_pollo
                FROM productos
                WHERE id = %s
                LIMIT 1
                """,
                (
                    producto_id,
                )
            )
            producto_db = (
                cursor.fetchone()
            )
            if not producto_db:
                raise Exception(
                    f'El producto con ID '
                    f'{producto_id} no existe.'
                )
            nombre_producto = (
                producto_db['nombre']
                or 'Producto'
            )
            categoria = (
                producto_db['categoria']
                or ''
            ).strip().lower()
            stock_actual = int(
                producto_db['stock']
                or 0
            )
            equivalencia_pollo = (
                producto_db.get(
                    'equivalencia_pollo'
                )
            )
            # =================================================
            # COMPROBAR Y RESERVAR STOCK
            # =================================================
            if equivalencia_pollo is not None:
                # -------------------------------------------------
                # EL STOCK DE POLLO SE MANEJA EXCLUSIVAMENTE
                # MEDIANTE reservar_stock_pollo()
                #
                # 1 pollo  = 8 octavos
                # 1/2      = 4 octavos
                # 1/4      = 2 octavos
                # 1/8      = 1 octavo
                # -------------------------------------------------
                stock_reservado = reservar_stock_pollo(
                    cursor,
                    producto_id,
                    cantidad
                )
                if not stock_reservado:
                    raise Exception(
                        f'No hay suficiente '
                        f'stock de pollo para '
                        f'{nombre_producto}.'
                    )
            else:
                # -------------------------------------------------
                # PRODUCTO NORMAL
                # -------------------------------------------------
                if stock_actual < cantidad:
                    raise Exception(
                        f'No hay suficiente '
                        f'stock de '
                        f'{nombre_producto}. '
                        f'Stock disponible: '
                        f'{stock_actual}.'
                    )
                # -------------------------------------------------
                # DESCONTAR PRODUCTO NORMAL
                # -------------------------------------------------
                cursor.execute(
                    """
                    UPDATE productos
                    SET stock = stock - %s
                    WHERE id = %s
                      AND stock >= %s
                    """,
                    (
                        cantidad,
                        producto_id,
                        cantidad
                    )
                )
                if cursor.rowcount == 0:
                    raise Exception(
                        f'No se pudo descontar '
                        f'el stock de '
                        f'{nombre_producto}.'
                    )
            # =================================================
            # USAR PRECIO DE BASE DE DATOS
            # =================================================
            precio = float(
                producto_db['precio']
                or precio_carrito
            )
            subtotal = round(
                precio * cantidad,
                2
            )
            # =================================================
            # INSERTAR DETALLE
            # =================================================
            cursor.execute(
                """
                INSERT INTO detalle_pedido
                (
                    pedido_id,
                    producto_id,
                    cantidad,
                    precio,
                    subtotal
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    pedido_id,
                    producto_id,
                    cantidad,
                    precio,
                    subtotal
                )
            )
            # =================================================
            # PRODUCTOS DE POLLO → POLLERO
            # =================================================
            if categoria in (
                'pollo',
                'pollos'
            ):
                cursor.execute(
                    """
                    INSERT INTO ordenes_cocina
                    (
                        mesa,
                        pedido_id,
                        producto_id,
                        producto,
                        cantidad,
                        estado,
                        observacion
                    )
                    VALUES
                    (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    )
                    """,
                    (
                        mesa_id,
                        pedido_id,
                        producto_id,
                        nombre_producto,
                        cantidad,
                        'pendiente',
                        observacion
                    )
                )
        # ========================================================
        # ACTUALIZAR MESA
        # ========================================================
        if mesa_id is not None:
            cursor.execute(
                """
                UPDATE mesas
                SET estado = 'ocupada'
                WHERE id = %s
                """,
                (
                    mesa_id,
                )
            )
        # ========================================================
        # CONFIRMAR TODO
        # ========================================================
        conexion.commit()
        # ========================================================
        # OBTENER PEDIDO CREADO
        # ========================================================
        cursor.execute(
            """
            SELECT *
            FROM pedidos
            WHERE id = %s
            LIMIT 1
            """,
            (
                pedido_id,
            )
        )
        pedido = cursor.fetchone()
        # ========================================================
        # LIMPIAR CARRITO
        # ========================================================
        session.pop(
            'carrito',
            None
        )
        session.pop(
            'pedido',
            None
        )
        # ========================================================
        # LIMPIAR DATOS TEMPORALES DE CULQI
        # ========================================================
        session.pop(
            'culqi_metodo',
            None
        )
        session.pop(
            'culqi_charge_id',
            None
        )
        # ========================================================
        # MOSTRAR BOLETA
        # ========================================================
        return render_template(
            'boleta_pago.html',
            pedido=pedido,
            metodo=metodo_pago_final,
            codigo_yape=None,
            monto_efectivo=monto_efectivo,
            vuelto=vuelto
        )
    # ========================================================
    # ERROR
    # ========================================================
    except Exception as e:
        if conexion:
            try:
                conexion.rollback()
            except Exception:
                pass
        print(
            "============================================"
        )
        print(
            "❌ ERROR AL FINALIZAR PEDIDO"
        )
        print(
            "❌ TIPO:",
            type(e).__name__
        )
        print(
            "❌ DETALLE:",
            str(e)
        )
        print(
            "============================================"
        )
        return f"""
        <!DOCTYPE html>
        <html lang="es">
        <head>
            <meta charset="UTF-8">
            <title>
                Error al finalizar pedido
            </title>
            <style>
                body {{
                    margin: 0;
                    padding: 40px;
                    background: #f7f7f6;
                    font-family: Arial, sans-serif;
                    color: #171717;
                }}
                .error {{
                    max-width: 850px;
                    margin: 60px auto;
                    background: white;
                    padding: 35px;
                    border-radius: 18px;
                    box-shadow:
                        0 15px 40px rgba(0,0,0,.08);
                }}
                h1 {{
                    color: #f47b20;
                }}
                .detalle {{
                    margin-top: 20px;
                    padding: 18px;
                    background: #fff0e2;
                    border-left:
                        4px solid #f47b20;
                    border-radius: 8px;
                    font-family: monospace;
                    white-space: pre-wrap;
                }}
                a {{
                    display: inline-block;
                    margin-top: 20px;
                    padding: 12px 20px;
                    background: #f47b20;
                    color: white;
                    text-decoration: none;
                    border-radius: 10px;
                    font-weight: bold;
                }}
            </style>
        </head>
        <body>
            <div class="error">
                <h1>
                    Error al finalizar el pedido
                </h1>
                <p>
                    No se pudo completar el pedido.
                </p>
                <div class="detalle">
                    {type(e).__name__}: {str(e)}
                </div>
                <a href="/carrito">
                    ← Volver al carrito
                </a>
            </div>
        </body>
        </html>
        """
    # ========================================================
    # CERRAR CONEXIÓN
    # ========================================================
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()

# ============================================================
# CAMBIAR ESTADO PEDIDO
# ============================================================

@app.route(
    '/cambiar_estado/<int:id>/<estado>'
)
def cambiar_estado(id, estado):

    if session.get('rol') != 'admin':
        return redirect('/login')            


    estado = estado.replace(
        "_",
        " "
    )


    conexion = get_connection()

    cursor = conexion.cursor(
        dictionary=True
    )


    cursor.execute(
        """
        SELECT estado
        FROM pedidos
        WHERE id=%s
        """,
        (id,)
    )


    pedido = cursor.fetchone()


    if (
        pedido
        and pedido['estado'] == 'Rechazado'
    ):

        cursor.close()
        conexion.close()

        return redirect(
            '/admin_pedidos'
        )


    cursor.execute(
        """
        UPDATE pedidos
        SET estado=%s
        WHERE id=%s
        """,
        (
            estado,
            id
        )
    )


    conexion.commit()


    cursor.close()
    conexion.close()


    return redirect(
        '/admin_pedidos'
    )


# ============================================================
# RESERVAS
# ============================================================

@app.route('/reserva')
def reserva():

    if 'usuario' not in session:
        return redirect('/login')


    fecha_minima = (
        date.today()
        + timedelta(days=1)
    ).isoformat()


    return render_template(
        'reserva.html',
        fecha_minima=fecha_minima
    )


@app.route(
    '/guardar_reserva',
    methods=['POST']
)
def guardar_reserva():

    if 'usuario' not in session:
        return redirect('/login')


    session['reserva'] = {

        'nombre':
            request.form.get('nombre'),

        'telefono':
            request.form.get('telefono'),

        'fecha':
            request.form.get('fecha'),

        'hora':
            request.form.get('hora'),

        'personas':
            request.form.get('personas')

    }


    datos = session.get(
        'reserva',
        {}
    )


    conexion = get_connection()

    cursor = conexion.cursor()


    cursor.execute(
        """
        INSERT INTO reservas
        (
            nombre,
            telefono,
            fecha,
            hora,
            personas,
            estado
        )
        VALUES
        (%s,%s,%s,%s,%s,%s)
        """,
        (
            datos.get('nombre')
            or session['usuario'],

            datos.get('telefono'),

            datos.get('fecha'),

            datos.get('hora'),

            datos.get('personas'),

            "Pendiente"
        )
    )


    conexion.commit()


    cursor.close()
    conexion.close()


    return redirect(
        '/mis_reservas'
    )


@app.route('/mis_reservas')
def mis_reservas():

    if 'usuario' not in session:
        return redirect('/login')


    datos = session.get(
        'reserva',
        {}
    )


    conexion = get_connection()

    cursor = conexion.cursor(
        dictionary=True
    )


    cursor.execute(
        """
        SELECT *
        FROM reservas

        WHERE nombre=%s
        OR telefono=%s

        ORDER BY id DESC
        """,
        (
            datos.get('nombre'),
            datos.get('telefono')
        )
    )


    reservas = cursor.fetchall()


    cursor.close()
    conexion.close()


    return render_template(
        'mis_reservas.html',
        reservas=reservas
    )


# ============================================================
# PAGO
# ============================================================

@app.route(
    '/pago',
    methods=['GET', 'POST']
)
def pago():

    if 'usuario' not in session:
        return redirect('/login')


    if request.method == 'POST':

        session['pedido'] = {

            'nombre':
                request.form.get('nombre'),

            'telefono':
                request.form.get('telefono'),

            'direccion':
                request.form.get('direccion'),

            'referencia':
                request.form.get('referencia')

        }


    # ========================================================
    # CARGAR CARRITO
    # ========================================================

    carrito = session.get('carrito', [])


    # ========================================================
    # CALCULAR TOTAL REAL
    # ========================================================

    total = sum(
        float(producto.get('precio', 0))
        * int(producto.get('cantidad', 1))
        for producto in carrito
    )


    # ========================================================
    # MOSTRAR PAGO + CULQI
    # ========================================================

    return render_template(
        'pago.html',
        carrito=carrito,
        total=total,
        culqi_public_key=CULQI_PUBLIC_KEY
    )

# ============================================================
# ADMIN RESERVAS
# ============================================================

@app.route('/admin_reservas')
def admin_reservas():

    if session.get('rol') != 'admin':
        return redirect('/login')


    conexion = get_connection()

    cursor = conexion.cursor(
        dictionary=True
    )


    cursor.execute(
        """
        SELECT *
        FROM reservas
        """
    )


    reservas = cursor.fetchall()


    cursor.close()
    conexion.close()


    return render_template(
        'admin_reservas.html',
        reservas=reservas
    )


# ============================================================
# CAMBIAR ESTADO RESERVA
# ============================================================

@app.route(
    '/cambiar_estado_reserva/<int:id>/<estado>',
    methods=['POST']
)
def cambiar_estado_reserva(
    id,
    estado
):

    if session.get('rol') != 'admin':
        return redirect('/login')


    conexion = get_connection()

    cursor = conexion.cursor()


    mensaje = ""


    if estado == "Confirmada":

        mensaje = (
            "✅ Reserva confirmada. "
            "Llegue 15 minutos antes."
        )


    elif estado == "Rechazada":

        mensaje = request.form.get(
            "mensaje"
        )


    cursor.execute(
        """
        UPDATE reservas

        SET
            estado=%s,
            mensaje=%s

        WHERE id=%s
        """,
        (
            estado,
            mensaje,
            id
        )
    )


    conexion.commit()


    cursor.close()
    conexion.close()


    return redirect(
        '/admin_reservas'
    )


# ============================================================
# CATERING
# ============================================================

@app.route('/catering')
def catering():

    if 'usuario' not in session:
        return redirect('/login')


    return render_template(
        'catering.html'
    )


# ============================================================
# NOSOTROS
# ============================================================

@app.route('/nosotros')
def nosotros():

    return render_template('nosotros.html')

# ============================================================
# CHECKOUT
# ============================================================

@app.route('/checkout')
def checkout():

    if 'usuario' not in session:
        return redirect('/login')

    if not session.get('carrito'):
        return redirect('/carta')

    conexion = None
    cursor = None

    try:

        conexion = get_connection()

        cursor = conexion.cursor(
            dictionary=True
        )

        # ====================================================
        # TIPOS DE DOCUMENTO
        # ====================================================

        cursor.execute(
            """
            SELECT
                id,
                nombre
            FROM tipos_documento
            WHERE activo = 1
            ORDER BY id ASC
            """
        )

        tipos_documento = cursor.fetchall()

        # ====================================================
        # TIPOS DE ENTREGA
        # ====================================================

        cursor.execute(
            """
            SELECT
                id,
                nombre
            FROM tipos_entrega
            WHERE activo = 1
            ORDER BY id ASC
            """
        )

        tipos_entrega = cursor.fetchall()

        # ====================================================
        # UBICACIONES
        # ====================================================

        cursor.execute(
            """
            SELECT
                id,
                departamento,
                provincia,
                distrito
            FROM ubicaciones
            WHERE activo = 1
            ORDER BY id ASC
            """
        )

        ubicaciones = cursor.fetchall()

        # ====================================================
        # ENVIAR DATOS AL CHECKOUT
        # ====================================================

        return render_template(
            'checkout.html',
            tipos_documento=tipos_documento,
            tipos_entrega=tipos_entrega,
            ubicaciones=ubicaciones
        )

    finally:

        if cursor:
            cursor.close()

        if conexion:
            conexion.close()

# ============================================================
# SEGUIMIENTO
# ============================================================

@app.route(
    '/seguimiento/<int:pedido_id>'
)
def seguimiento(pedido_id):

    if 'usuario' not in session:
        return redirect('/login')


    conexion = get_connection()

    cursor = conexion.cursor(
        dictionary=True
    )


    cursor.execute(
        """
        SELECT *
        FROM pedidos
        WHERE id=%s
        """,
        (pedido_id,)
    )


    pedido = cursor.fetchone()


    cursor.close()
    conexion.close()


    return render_template(
        'seguimiento.html',
        pedido=pedido
    )


# ============================================================
# MIS PEDIDOS
# ============================================================

@app.route('/mis_pedidos')
def mis_pedidos():

    if 'usuario' not in session:
        return redirect('/login')


    conexion = get_connection()

    cursor = conexion.cursor(
        dictionary=True
    )


    cursor.execute(
        """
        SELECT *
        FROM pedidos
        ORDER BY id DESC
        """
    )


    pedidos = cursor.fetchall()


    cursor.close()
    conexion.close()


    return render_template(
        'mis_pedidos.html',
        pedidos=pedidos
    )


# ============================================================
# CAMBIAR CONTRASEÑA
# ============================================================

@app.route('/cambiar_contrasena', methods=['GET', 'POST'])
def cambiar_contrasena():

    # Verificar sesión
    if 'usuario' not in session:
        return redirect('/login')

    # Procesar formulario
    if request.method == 'POST':

        nueva = request.form.get('nueva', '').strip()

        # Verificar que no esté vacío
        if not nueva:
            return render_template(
                'cambiar_clave.html',
                error='La nueva contraseña es obligatoria.'
            )

        # Encriptar contraseña
        nueva_hash = generate_password_hash(nueva)

        conexion = get_connection()
        cursor = conexion.cursor()

        try:

            cursor.execute(
                """
                UPDATE usuarios
                SET clave=%s
                WHERE correo=%s
                """,
                (
                    nueva_hash,
                    session['usuario']
                )
            )

            conexion.commit()

        finally:

            cursor.close()
            conexion.close()

        # Después de cambiar la contraseña
        return redirect('/login')

    # Mostrar formulario
    return render_template(
        'cambiar_clave.html'
    )

# ============================================================
# INVENTARIO
# ============================================================

@app.route('/inventario')
def inventario():

    # --------------------------------------------------------
    # VERIFICAR ADMINISTRADOR
    # --------------------------------------------------------

    if session.get('rol') != 'admin':
        return redirect('/login')

    conexion = None
    cursor = None

    try:

        conexion = get_connection()

        cursor = conexion.cursor(
            dictionary=True
        )

        # ----------------------------------------------------
        # OBTENER PRODUCTOS
        # ----------------------------------------------------

        cursor.execute("""
            SELECT
                id,
                nombre,
                precio,
                stock,
                categoria,
                imagen,
                equivalencia_pollo
            FROM productos
            ORDER BY id DESC
        """)

        productos = cursor.fetchall()

        # ----------------------------------------------------
        # OBTENER STOCK REAL DE POLLO
        # ----------------------------------------------------

        cursor.execute("""
            SELECT
                pollos_enteros,
                medios_pollos,
                cuartos_pollos,
                octavos_pollos
            FROM stock_pollo
            WHERE id = 1
            LIMIT 1
        """)

        stock_pollo = cursor.fetchone()

        # ----------------------------------------------------
        # SI NO EXISTE STOCK
        # ----------------------------------------------------

        if not stock_pollo:

            stock_pollo = {
                'pollos_enteros': 0,
                'medios_pollos': 0,
                'cuartos_pollos': 0,
                'octavos_pollos': 0
            }

        # ----------------------------------------------------
        # CONVERTIR TODO EL STOCK A OCTAVOS
        #
        # 1 entero = 8 octavos
        # 1/2      = 4 octavos
        # 1/4      = 2 octavos
        # 1/8      = 1 octavo
        # ----------------------------------------------------

        pollos_enteros = int(
            stock_pollo.get('pollos_enteros') or 0
        )

        medios_pollos = int(
            stock_pollo.get('medios_pollos') or 0
        )

        cuartos_pollos = int(
            stock_pollo.get('cuartos_pollos') or 0
        )

        octavos_pollos = int(
            stock_pollo.get('octavos_pollos') or 0
        )

        total_octavos = (
            (pollos_enteros * 8)
            +
            (medios_pollos * 4)
            +
            (cuartos_pollos * 2)
            +
            octavos_pollos
        )

        # ----------------------------------------------------
        # STOCK FÍSICO REAL
        #
        # EJEMPLO:
        # 160 octavos = 20 pollos
        # 156 octavos = 19.5 pollos
        # 154 octavos = 19.25 pollos
        # ----------------------------------------------------

        stock_real_pollo = round(
            total_octavos / 8,
            3
        )

        # ----------------------------------------------------
        # CALCULAR STOCK DE CADA PRESENTACIÓN
        #
        # ENTERO = 8 octavos
        # MEDIO   = 4 octavos
        # CUARTO  = 2 octavos
        # OCTAVO  = 1 octavo
        #
        # SE USA // PARA MOSTRAR SOLAMENTE UNIDADES COMPLETAS
        # ----------------------------------------------------

        stock_enteros = total_octavos // 8

        stock_medios = total_octavos // 4

        stock_cuartos = total_octavos // 2

        stock_octavos = total_octavos

        # ----------------------------------------------------
        # DETERMINAR STOCK DE CADA PRODUCTO
        # ----------------------------------------------------

        for producto in productos:

            equivalencia = producto.get(
                'equivalencia_pollo'
            )

            if equivalencia is not None:

                try:

                    equivalencia = int(
                        equivalencia
                    )

                except (
                    TypeError,
                    ValueError
                ):

                    equivalencia = None

            # ------------------------------------------------
            # POLLO ENTERO
            # ------------------------------------------------

            if equivalencia == 8:

                producto['stock_mostrar'] = (
                    stock_enteros
                )

                producto['stock_real_pollo'] = (
                    stock_real_pollo
                )

            # ------------------------------------------------
            # 1/2 POLLO
            # ------------------------------------------------

            elif equivalencia == 4:

                producto['stock_mostrar'] = (
                    stock_medios
                )

                producto['stock_real_pollo'] = (
                    stock_real_pollo
                )

            # ------------------------------------------------
            # 1/4 POLLO
            # ------------------------------------------------

            elif equivalencia == 2:

                producto['stock_mostrar'] = (
                    stock_cuartos
                )

                producto['stock_real_pollo'] = (
                    stock_real_pollo
                )

            # ------------------------------------------------
            # 1/8 POLLO
            # ------------------------------------------------

            elif equivalencia == 1:

                producto['stock_mostrar'] = (
                    stock_octavos
                )

                producto['stock_real_pollo'] = (
                    stock_real_pollo
                )

            # ------------------------------------------------
            # PRODUCTO NORMAL
            # ------------------------------------------------

            else:

                producto['stock_mostrar'] = int(
                    producto.get('stock') or 0
                )

                producto['stock_real_pollo'] = None

        # ----------------------------------------------------
        # ENVIAR AL HTML
        # ----------------------------------------------------

        return render_template(
            'inventario.html',
            productos=productos,
            stock_pollo=stock_pollo,
            stock_real_pollo=stock_real_pollo,
            total_octavos=total_octavos
        )

    except Exception as e:

        print(
            "========================================"
        )

        print(
            "ERROR EN INVENTARIO"
        )

        print(
            "TIPO:",
            type(e).__name__
        )

        print(
            "DETALLE:",
            str(e)
        )

        print(
            "========================================"
        )

        return f"""
        <h2>Error al cargar el inventario</h2>

        <pre>{e}</pre>

        <br>

        <a href="/admin">
            Volver al administrador
        </a>
        """

    finally:

        if cursor:
            cursor.close()

        if conexion:
            conexion.close()


# ============================================================
# SUMAR STOCK
# ============================================================

@app.route('/inventario/sumar/<int:id>')
def sumar_stock(id):

    # --------------------------------------------------------
    # VERIFICAR SESIÓN
    # --------------------------------------------------------

    if not session.get('usuario'):
        return redirect('/login')

    # --------------------------------------------------------
    # VERIFICAR ADMIN
    # --------------------------------------------------------

    if session.get('rol') != 'admin':
        return redirect('/carta')

    conexion = None
    cursor = None

    try:

        conexion = get_connection()

        cursor = conexion.cursor(
            dictionary=True
        )

        # ----------------------------------------------------
        # OBTENER PRODUCTO
        # ----------------------------------------------------

        cursor.execute("""
            SELECT
                id,
                equivalencia_pollo
            FROM productos
            WHERE id = %s
            FOR UPDATE
        """, (id,))

        producto = cursor.fetchone()

        if not producto:

            conexion.rollback()

            return redirect('/inventario')

        equivalencia = producto.get(
            'equivalencia_pollo'
        )

        if equivalencia is not None:

            try:

                equivalencia = int(
                    equivalencia
                )

            except (
                TypeError,
                ValueError
            ):

                equivalencia = None

        # ====================================================
        # PRODUCTO DE POLLO
        # ====================================================

        if equivalencia in (1, 2, 4, 8):

            # ------------------------------------------------
            # OBTENER STOCK ACTUAL
            # ------------------------------------------------

            cursor.execute("""
                SELECT
                    pollos_enteros,
                    medios_pollos,
                    cuartos_pollos,
                    octavos_pollos
                FROM stock_pollo
                WHERE id = 1
                FOR UPDATE
            """)

            stock_pollo = cursor.fetchone()

            if not stock_pollo:

                conexion.rollback()

                return redirect('/inventario')

            # ------------------------------------------------
            # CONVERTIR STOCK ACTUAL A OCTAVOS
            # ------------------------------------------------

            total_octavos = (
                (int(
                    stock_pollo.get(
                        'pollos_enteros'
                    ) or 0
                ) * 8)
                +
                (int(
                    stock_pollo.get(
                        'medios_pollos'
                    ) or 0
                ) * 4)
                +
                (int(
                    stock_pollo.get(
                        'cuartos_pollos'
                    ) or 0
                ) * 2)
                +
                int(
                    stock_pollo.get(
                        'octavos_pollos'
                    ) or 0
                )
            )

            # ------------------------------------------------
            # AÑADIR SEGÚN LA PRESENTACIÓN
            # ------------------------------------------------

            unidades_a_sumar = {
                8: 8,
                4: 4,
                2: 2,
                1: 1
            }

            total_octavos += unidades_a_sumar[
                equivalencia
            ]

            # ------------------------------------------------
            # VOLVER A DESCOMPONER EL STOCK
            # ------------------------------------------------

            nuevos_enteros = total_octavos // 8

            resto = total_octavos % 8

            nuevos_medios = resto // 4

            resto = resto % 4

            nuevos_cuartos = resto // 2

            nuevos_octavos = resto % 2

            # ------------------------------------------------
            # GUARDAR STOCK
            # ------------------------------------------------

            cursor.execute("""
                UPDATE stock_pollo
                SET
                    pollos_enteros = %s,
                    medios_pollos = %s,
                    cuartos_pollos = %s,
                    octavos_pollos = %s
                WHERE id = 1
            """, (
                nuevos_enteros,
                nuevos_medios,
                nuevos_cuartos,
                nuevos_octavos
            ))

        # ====================================================
        # PRODUCTO NORMAL
        # ====================================================

        else:

            cursor.execute("""
                UPDATE productos
                SET stock =
                    COALESCE(stock, 0) + 1
                WHERE id = %s
            """, (id,))

        # ----------------------------------------------------
        # CONFIRMAR
        # ----------------------------------------------------

        conexion.commit()

    except Exception as e:

        if conexion:
            conexion.rollback()

        print(
            "========================================"
        )

        print(
            "ERROR AL SUMAR STOCK"
        )

        print(
            "TIPO:",
            type(e).__name__
        )

        print(
            "DETALLE:",
            str(e)
        )

        print(
            "========================================"
        )

    finally:

        if cursor:
            cursor.close()

        if conexion:
            conexion.close()

    return redirect('/inventario')


# ============================================================
# RESTAR STOCK
# ============================================================

@app.route('/inventario/restar/<int:id>')
def restar_stock(id):

    # --------------------------------------------------------
    # VERIFICAR SESIÓN
    # --------------------------------------------------------

    if not session.get('usuario'):
        return redirect('/login')

    # --------------------------------------------------------
    # VERIFICAR ADMIN
    # --------------------------------------------------------

    if session.get('rol') != 'admin':
        return redirect('/carta')

    conexion = None
    cursor = None

    try:

        conexion = get_connection()

        cursor = conexion.cursor(
            dictionary=True
        )

        # ----------------------------------------------------
        # OBTENER PRODUCTO
        # ----------------------------------------------------

        cursor.execute("""
            SELECT
                id,
                equivalencia_pollo
            FROM productos
            WHERE id = %s
            FOR UPDATE
        """, (id,))

        producto = cursor.fetchone()

        if not producto:

            conexion.rollback()

            return redirect('/inventario')

        equivalencia = producto.get(
            'equivalencia_pollo'
        )

        if equivalencia is not None:

            try:

                equivalencia = int(
                    equivalencia
                )

            except (
                TypeError,
                ValueError
            ):

                equivalencia = None

        # ====================================================
        # PRODUCTO DE POLLO
        # ====================================================

        if equivalencia in (1, 2, 4, 8):

            # ------------------------------------------------
            # OBTENER STOCK ACTUAL
            # ------------------------------------------------

            cursor.execute("""
                SELECT
                    pollos_enteros,
                    medios_pollos,
                    cuartos_pollos,
                    octavos_pollos
                FROM stock_pollo
                WHERE id = 1
                FOR UPDATE
            """)

            stock_pollo = cursor.fetchone()

            if not stock_pollo:

                conexion.rollback()

                return redirect('/inventario')

            # ------------------------------------------------
            # CONVERTIR TODO A OCTAVOS
            # ------------------------------------------------

            total_octavos = (
                (int(
                    stock_pollo.get(
                        'pollos_enteros'
                    ) or 0
                ) * 8)
                +
                (int(
                    stock_pollo.get(
                        'medios_pollos'
                    ) or 0
                ) * 4)
                +
                (int(
                    stock_pollo.get(
                        'cuartos_pollos'
                    ) or 0
                ) * 2)
                +
                int(
                    stock_pollo.get(
                        'octavos_pollos'
                    ) or 0
                )
            )

            # ------------------------------------------------
            # DETERMINAR CUÁNTO DESCONTAR
            # ------------------------------------------------

            unidades_a_restar = {
                8: 8,
                4: 4,
                2: 2,
                1: 1
            }

            descuento = unidades_a_restar[
                equivalencia
            ]

            # ------------------------------------------------
            # VERIFICAR STOCK SUFICIENTE
            # ------------------------------------------------

            if total_octavos < descuento:

                conexion.rollback()

                return redirect('/inventario')

            # ------------------------------------------------
            # RESTAR DEL STOCK FÍSICO
            # ------------------------------------------------

            total_octavos -= descuento

            # ------------------------------------------------
            # VOLVER A DESCOMPONER
            # ------------------------------------------------

            nuevos_enteros = total_octavos // 8

            resto = total_octavos % 8

            nuevos_medios = resto // 4

            resto = resto % 4

            nuevos_cuartos = resto // 2

            nuevos_octavos = resto % 2

            # ------------------------------------------------
            # GUARDAR
            # ------------------------------------------------

            cursor.execute("""
                UPDATE stock_pollo
                SET
                    pollos_enteros = %s,
                    medios_pollos = %s,
                    cuartos_pollos = %s,
                    octavos_pollos = %s
                WHERE id = 1
            """, (
                nuevos_enteros,
                nuevos_medios,
                nuevos_cuartos,
                nuevos_octavos
            ))

        # ====================================================
        # PRODUCTO NORMAL
        # ====================================================

        else:

            cursor.execute("""
                UPDATE productos
                SET stock = stock - 1
                WHERE id = %s
                  AND COALESCE(stock, 0) > 0
            """, (id,))

            if cursor.rowcount == 0:

                conexion.rollback()

                return redirect('/inventario')

        # ----------------------------------------------------
        # CONFIRMAR
        # ----------------------------------------------------

        conexion.commit()

    except Exception as e:

        if conexion:
            conexion.rollback()

        print(
            "========================================"
        )

        print(
            "ERROR AL RESTAR STOCK"
        )

        print(
            "TIPO:",
            type(e).__name__
        )

        print(
            "DETALLE:",
            str(e)
        )

        print(
            "========================================"
        )

    finally:

        if cursor:
            cursor.close()

        if conexion:
            conexion.close()

    return redirect('/inventario')


            
# ============================================================
# AGREGAR PRODUCTO
# ============================================================

@app.route('/inventario/agregar', methods=['GET', 'POST'])
def agregar_producto():

    # --------------------------------------------------------
    # VERIFICAR SESIÓN
    # --------------------------------------------------------
    if not session.get('usuario'):
        return redirect('/login')

    # --------------------------------------------------------
    # VERIFICAR ADMINISTRADOR
    # --------------------------------------------------------
    if session.get('rol') != 'admin':
        return redirect('/carta')

    # ========================================================
    # GUARDAR PRODUCTO
    # ========================================================

    if request.method == 'POST':

        nombre = request.form.get('nombre', '').strip()
        precio = request.form.get('precio', '').strip()
        stock = request.form.get('stock', '0').strip()
        categoria = request.form.get('categoria', '').strip()
        equivalencia_pollo = request.form.get(
            'equivalencia_pollo',
            ''
        ).strip()

        archivo_imagen = request.files.get('imagen')

        conexion = None
        cursor = None
        nombre_imagen = None
        ruta_imagen = None

        try:

            # ------------------------------------------------
            # VALIDAR DATOS
            # ------------------------------------------------

            if not nombre:
                return """
                <h2>Error</h2>
                <p>El nombre del producto es obligatorio.</p>
                <a href="/inventario/agregar">← Volver</a>
                """

            if not precio:
                return """
                <h2>Error</h2>
                <p>El precio es obligatorio.</p>
                <a href="/inventario/agregar">← Volver</a>
                """

            try:
                precio = float(precio)

                if precio < 0:
                    raise ValueError

            except ValueError:
                return """
                <h2>Error</h2>
                <p>El precio no es válido.</p>
                <a href="/inventario/agregar">← Volver</a>
                """

            try:
                stock = int(stock)

                if stock < 0:
                    raise ValueError

            except ValueError:
                return """
                <h2>Error</h2>
                <p>El stock debe ser un número entero mayor o igual a 0.</p>
                <a href="/inventario/agregar">← Volver</a>
                """

            # ------------------------------------------------
            # EQUIVALENCIA DE POLLO
            # ------------------------------------------------

            if equivalencia_pollo in ('1', '2', '4', '8'):
                equivalencia_pollo = int(equivalencia_pollo)

            else:
                equivalencia_pollo = None

            # ------------------------------------------------
            # CREAR CARPETA DE IMÁGENES
            # ------------------------------------------------

            carpeta_imagenes = os.path.join(
                app.static_folder,
                'img'
            )

            os.makedirs(
                carpeta_imagenes,
                exist_ok=True
            )

            # ------------------------------------------------
            # SUBIR IMAGEN
            # ------------------------------------------------

            if archivo_imagen and archivo_imagen.filename:

                extension = os.path.splitext(
                    archivo_imagen.filename
                )[1].lower()

                extensiones_permitidas = {
                    '.jpg',
                    '.jpeg',
                    '.png',
                    '.webp',
                    '.gif'
                }

                if extension not in extensiones_permitidas:
                    return """
                    <h2>Error</h2>
                    <p>
                        Formato de imagen no permitido.
                        Usa JPG, JPEG, PNG, WEBP o GIF.
                    </p>
                    <a href="/inventario/agregar">
                        ← Volver
                    </a>
                    """

                nombre_original = secure_filename(
                    archivo_imagen.filename
                )

                if not nombre_original:
                    return """
                    <h2>Error</h2>
                    <p>El nombre de la imagen no es válido.</p>
                    <a href="/inventario/agregar">
                        ← Volver
                    </a>
                    """

                # ------------------------------------------------
                # EVITAR SOBRESCRIBIR IMÁGENES
                # ------------------------------------------------

                nombre_base = os.path.splitext(
                    nombre_original
                )[0]

                nombre_base = secure_filename(
                    nombre_base
                )

                nombre_imagen = (
                    nombre_base
                    + '_'
                    + datetime.now().strftime('%Y%m%d%H%M%S%f')
                    + extension
                )

                ruta_imagen = os.path.join(
                    carpeta_imagenes,
                    nombre_imagen
                )

                archivo_imagen.save(ruta_imagen)

            # ------------------------------------------------
            # CONECTAR BASE DE DATOS
            # ------------------------------------------------

            conexion = get_connection()

            cursor = conexion.cursor()

            # ------------------------------------------------
            # INSERTAR PRODUCTO
            # ------------------------------------------------

            cursor.execute("""
                INSERT INTO productos
                (
                    nombre,
                    precio,
                    stock,
                    imagen,
                    categoria,
                    equivalencia_pollo
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
            """, (
                nombre,
                precio,
                stock,
                nombre_imagen,
                categoria,
                equivalencia_pollo
            ))

            conexion.commit()

            print("========================================")
            print("PRODUCTO AGREGADO CORRECTAMENTE")
            print("Nombre:", nombre)
            print("Precio:", precio)
            print("Stock:", stock)
            print("Categoría:", categoria)
            print("Imagen:", nombre_imagen)
            print("Equivalencia:", equivalencia_pollo)
            print("========================================")

            return redirect('/inventario')

        except Exception as e:

            # ------------------------------------------------
            # DESHACER BASE DE DATOS
            # ------------------------------------------------

            if conexion:
                conexion.rollback()

            # ------------------------------------------------
            # SI FALLÓ LA BD, ELIMINAR IMAGEN SUBIDA
            # ------------------------------------------------

            if ruta_imagen and os.path.exists(ruta_imagen):

                try:
                    os.remove(ruta_imagen)

                except Exception:
                    pass

            print("========================================")
            print("ERROR AL AGREGAR PRODUCTO")
            print("========================================")
            print(e)

            return f"""
            <h2>Error al agregar producto</h2>

            <pre>{e}</pre>

            <br>

            <a href="/inventario/agregar">
                ← Volver a agregar producto
            </a>
            """

        finally:

            if cursor:
                cursor.close()

            if conexion:
                conexion.close()

    # ========================================================
    # MOSTRAR FORMULARIO
    # ========================================================

    return render_template(
        'agregar_producto.html'
    )


# ============================================================
# ELIMINAR PRODUCTO
# ============================================================

@app.route('/inventario/eliminar/<int:id>')
def eliminar_producto(id):

    # --------------------------------------------------------
    # VERIFICAR SESIÓN
    # --------------------------------------------------------
    if not session.get('usuario'):
        return redirect('/login')

    # --------------------------------------------------------
    # VERIFICAR ADMINISTRADOR
    # --------------------------------------------------------
    if session.get('rol') != 'admin':
        return redirect('/carta')

    conexion = None
    cursor = None
    nombre_imagen = None

    try:

        conexion = get_connection()

        cursor = conexion.cursor(dictionary=True)

        # ----------------------------------------------------
        # OBTENER PRODUCTO E IMAGEN
        # ----------------------------------------------------

        cursor.execute("""
            SELECT
                id,
                imagen
            FROM productos
            WHERE id = %s
        """, (id,))

        producto = cursor.fetchone()

        if not producto:
            return redirect('/inventario')

        nombre_imagen = producto.get('imagen')

        # ----------------------------------------------------
        # ELIMINAR PRODUCTO DE LA BASE DE DATOS
        # ----------------------------------------------------

        cursor.execute("""
            DELETE FROM productos
            WHERE id = %s
        """, (id,))

        conexion.commit()

        # ----------------------------------------------------
        # ELIMINAR IMAGEN DEL SERVIDOR
        # ----------------------------------------------------

        if nombre_imagen:

            ruta_imagen = os.path.join(
                app.static_folder,
                'img',
                nombre_imagen
            )

            if os.path.exists(ruta_imagen):

                try:
                    os.remove(ruta_imagen)

                except Exception as error_imagen:

                    print(
                        "No se pudo eliminar la imagen:",
                        error_imagen
                    )

        print("Producto eliminado:", id)

    except Exception as e:

        if conexion:
            conexion.rollback()

        print("========================================")
        print("ERROR AL ELIMINAR PRODUCTO")
        print("========================================")
        print(e)

    finally:

        if cursor:
            cursor.close()

        if conexion:
            conexion.close()

    return redirect('/inventario')



# ============================================================
# ADMIN PEDIDOS
# ============================================================

@app.route('/admin_pedidos')
def admin_pedidos():

    if session.get('rol') != 'admin':
        return redirect('/login')


    conexion = get_connection()

    cursor = conexion.cursor(
        dictionary=True
    )


    cursor.execute(
        """
        SELECT *
        FROM pedidos
        ORDER BY id DESC
        """
    )


    pedidos = cursor.fetchall()


    cursor.close()
    conexion.close()


    return render_template(
        'admin_pedidos.html',
        pedidos=pedidos
    )

# ============================================================
# USUARIOS CLIENTES - ADMINISTRADOR
# ============================================================

@app.route('/admin_usuarios')
def admin_usuarios():

    if session.get('rol') != 'admin':
        return redirect('/login')

    conexion = get_connection()
    cursor = conexion.cursor(dictionary=True)

    try:

        cursor.execute("""
            SELECT
                id,
                nombres,
                apellidos,
                correo,
                telefono,
                ultimo_acceso,
                activo,
                DATEDIFF(NOW(), ultimo_acceso) AS dias_inactivo
            FROM usuarios
            WHERE rol = 'cliente'
            ORDER BY
                activo DESC,
                ultimo_acceso DESC
        """)

        usuarios = cursor.fetchall()

        return render_template(
            'admin_usuarios.html',
            usuarios=usuarios
        )

    finally:

        cursor.close()
        conexion.close()


# ============================================================
# ELIMINAR USUARIO CLIENTE
# ============================================================

@app.route('/admin_usuarios/eliminar/<int:id>')
def eliminar_usuario_cliente(id):

    if session.get('rol') != 'admin':
        return redirect('/login')

    conexion = get_connection()
    cursor = conexion.cursor(dictionary=True)

    try:

        # Verificar que realmente sea un cliente
        cursor.execute("""
            SELECT id
            FROM usuarios
            WHERE id = %s
            AND rol = 'cliente'
        """, (id,))

        usuario = cursor.fetchone()

        if not usuario:
            return redirect('/admin_usuarios')

        # Eliminar solamente al cliente
        cursor.execute("""
            DELETE FROM usuarios
            WHERE id = %s
            AND rol = 'cliente'
        """, (id,))

        conexion.commit()

    except Exception as e:

        conexion.rollback()

        print(
            "❌ ERROR AL ELIMINAR CLIENTE:",
            e
        )

    finally:

        cursor.close()
        conexion.close()

    return redirect('/admin_usuarios')

# ============================================================
# ADMIN TRABAJADORES
# ============================================================

@app.route('/admin_trabajadores')
def admin_trabajadores():

    if session.get('rol') != 'admin':
        return redirect('/login')


    conexion = get_connection()

    cursor = conexion.cursor(
        dictionary=True
    )


    cursor.execute(
        """
        SELECT

            t.id,
            t.usuario_id,
            t.nombre,
            t.cargo,
            t.telefono,
            t.correo,
            u.rol

        FROM trabajadores t

        LEFT JOIN usuarios u
            ON t.usuario_id=u.id

        ORDER BY t.id DESC
        """
    )


    trabajadores = cursor.fetchall()


    cursor.close()
    conexion.close()


    return render_template(
        'admin_trabajadores.html',
        trabajadores=trabajadores
    )


# ============================================================
# AGREGAR TRABAJADOR
# ============================================================

@app.route(
    '/trabajadores/agregar',
    methods=['GET', 'POST']
)
def agregar_trabajador():

    if session.get('rol') != 'admin':
        return redirect('/login')


    if request.method == 'POST':

        nombres = request.form.get(
            'nombres',
            ''
        ).strip()


        apellidos = request.form.get(
            'apellidos',
            ''
        ).strip()


        correo = request.form.get(
            'correo',
            ''
        ).strip().lower()


        cargo = request.form.get(
            'cargo',
            ''
        ).strip().lower()


        telefono = request.form.get(
            'telefono',
            ''
        ).strip()


        password = request.form.get(
            'password',
            ''
        )


        if (
            not nombres
            or not apellidos
            or not correo
        ):

            return "Completa todos los campos."


        if not correo.endswith(
            '@mail.com'
        ):

            return (
                "El correo debe terminar "
                "en @mail.com."
            )


        cargos_permitidos = [

            'mozo',
            'pollero',
            'cajero',
            'delivery',
            'gerente'

        ]


        if cargo not in cargos_permitidos:

            return "Cargo no válido."


        if len(password) < 6:

            return (
                "La contraseña debe tener "
                "mínimo 6 caracteres."
            )


        conexion = get_connection()

        cursor = conexion.cursor()


        try:

            cursor.execute(
                """
                SELECT id
                FROM usuarios
                WHERE correo=%s
                """,
                (correo,)
            )


            usuario_existente = \
                cursor.fetchone()


            if usuario_existente:

                return (
                    "Ese correo ya está registrado."
                )


            password_hash = \
                generate_password_hash(
                    password
                )


            cursor.execute(
                """
                INSERT INTO usuarios
                (
                    nombres,
                    apellidos,
                    correo,
                    clave,
                    rol
                )
                VALUES
                (%s,%s,%s,%s,%s)
                """,
                (
                    nombres,
                    apellidos,
                    correo,
                    password_hash,
                    cargo
                )
            )


            usuario_id = cursor.lastrowid


            nombre_completo = (
                f"{nombres} {apellidos}"
            )


            cursor.execute(
                """
                INSERT INTO trabajadores
                (
                    usuario_id,
                    nombre,
                    cargo,
                    telefono,
                    correo
                )
                VALUES
                (%s,%s,%s,%s,%s)
                """,
                (
                    usuario_id,
                    nombre_completo,
                    cargo,
                    telefono,
                    correo
                )
            )


            conexion.commit()


            return redirect(
                '/admin_trabajadores'
            )


        except Exception as e:

            conexion.rollback()

            print(
                "❌ ERROR AL CREAR TRABAJADOR:"
            )

            print(e)

            return (
                "Error al crear trabajador. "
                "Revisa la terminal."
            )


        finally:

            cursor.close()
            conexion.close()


    return render_template(
        'agregar_trabajador.html'
    )


# ============================================================
# ELIMINAR TRABAJADOR
# ============================================================

@app.route(
    '/trabajadores/eliminar/<int:id>'
)
def eliminar_trabajador(id):

    if session.get('rol') != 'admin':
        return redirect('/login')


    conexion = get_connection()

    cursor = conexion.cursor()


    try:

        cursor.execute(
            """
            SELECT usuario_id
            FROM trabajadores
            WHERE id=%s
            """,
            (id,)
        )


        trabajador = cursor.fetchone()


        if trabajador:

            usuario_id = trabajador[0]


            cursor.execute(
                """
                DELETE FROM trabajadores
                WHERE id=%s
                """,
                (id,)
            )


            if usuario_id:

                cursor.execute(
                    """
                    DELETE FROM usuarios
                    WHERE id=%s
                    """,
                    (usuario_id,)
                )


        conexion.commit()


    except Exception as e:

        conexion.rollback()

        print(
            "❌ ERROR AL ELIMINAR TRABAJADOR:"
        )

        print(e)


    finally:

        cursor.close()
        conexion.close()


    return redirect(
        '/admin_trabajadores'
    )


# ============================================================
# PANEL DEL MOZO
# ============================================================

@app.route('/panel_mozo')
def panel_mozo():

    if session.get('rol') != 'mozo':
        return redirect(url_for('login'))

    conexion = None
    cursor = None

    try:

        conexion = get_connection()
        cursor = conexion.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                id,
                numero,
                estado
            FROM mesas
            ORDER BY numero ASC
        """)

        mesas = cursor.fetchall()

        return render_template(
            'panel_mozo.html',
            mesas=mesas
        )

    except Exception as e:

        print("ERROR PANEL MOZO:", e)

        return f"""
        <h1>Error al cargar el panel del mozo</h1>
        <p>{str(e)}</p>
        """, 500

    finally:

        if cursor:
            cursor.close()

        if conexion:
            conexion.close()


# ============================================================
# LISTA DE MESAS DEL MOZO
# ============================================================

@app.route('/mozo/mesas')
def mozo_mesas():

    if session.get('rol') != 'mozo':
        return redirect(url_for('login'))

    conexion = None
    cursor = None

    try:

        conexion = get_connection()
        cursor = conexion.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                id,
                numero,
                estado
            FROM mesas
            ORDER BY numero ASC
        """)

        mesas = cursor.fetchall()

        return render_template(
            'mozo_mesas.html',
            mesas=mesas
        )

    except Exception as e:

        print("ERROR AL CARGAR MESAS:", e)

        return f"""
        <h1>Error al cargar las mesas</h1>

        <p>
            <strong>Tipo:</strong>
            {type(e).__name__}
        </p>

        <p>
            <strong>Detalle:</strong>
            {str(e)}
        </p>
        """, 500

    finally:

        if cursor:
            cursor.close()

        if conexion:
            conexion.close()


# ============================================================
# DETALLE DE MESA - MOZO
# ============================================================

@app.route('/mozo/mesa/<int:mesa_id>')
def mozo_mesa(mesa_id):

    if session.get('rol') != 'mozo':
        return redirect(url_for('login'))

    conexion = None
    cursor = None

    try:

        conexion = get_connection()
        cursor = conexion.cursor(dictionary=True)

        # ----------------------------------------------------
        # 1. OBTENER MESA
        # ----------------------------------------------------

        cursor.execute("""
            SELECT
                id,
                numero,
                estado
            FROM mesas
            WHERE id = %s
            LIMIT 1
        """, (mesa_id,))

        mesa = cursor.fetchone()

        if not mesa:
            return "Mesa no encontrada", 404

        # ----------------------------------------------------
        # 2. OBTENER PRODUCTOS
        # ----------------------------------------------------
        #
        # IMPORTANTE:
        # Los productos normales utilizan productos.stock.
        #
        # Los productos de pollo utilizan:
        # stock_pollo
        #
        # Por eso NO debemos utilizar únicamente:
        #
        #     WHERE stock > 0
        #
        # porque los productos de pollo pueden tener
        # productos.stock = 0 y aun así tener stock
        # disponible en stock_pollo.
        # ----------------------------------------------------

        cursor.execute("""
            SELECT
                id,
                nombre,
                precio,
                stock,
                categoria,
                imagen,
                equivalencia_pollo
            FROM productos
            WHERE stock > 0
               OR equivalencia_pollo IS NOT NULL
            ORDER BY categoria ASC, nombre ASC
        """)

        productos = cursor.fetchall()

        # ----------------------------------------------------
        # 3. OBTENER STOCK CENTRAL DE POLLO
        # ----------------------------------------------------

        cursor.execute("""
            SELECT
                pollos_enteros,
                medios_pollos,
                cuartos_pollos,
                octavos_pollos
            FROM stock_pollo
            WHERE id = 1
            LIMIT 1
        """)

        stock_pollo = cursor.fetchone()

        # ----------------------------------------------------
        # SI NO EXISTE STOCK CENTRAL
        # ----------------------------------------------------

        if not stock_pollo:

            stock_pollo = {
                'pollos_enteros': 0,
                'medios_pollos': 0,
                'cuartos_pollos': 0,
                'octavos_pollos': 0
            }

        # ----------------------------------------------------
        # 4. PREPARAR STOCK QUE MOSTRARÁ EL MOZO
        # ----------------------------------------------------

        productos_validos = []

        for producto in productos:

            equivalencia = producto.get(
                'equivalencia_pollo'
            )

            # =================================================
            # PRODUCTOS DE POLLO
            # =================================================

            if equivalencia is not None:

                equivalencia = int(
                    equivalencia
                )

                # ---------------------------------------------
                # POLLO ENTERO
                # ---------------------------------------------

                if equivalencia == 8:

                    producto['stock_mostrar'] = int(
                        stock_pollo['pollos_enteros'] or 0
                    )

                # ---------------------------------------------
                # MEDIO POLLO
                # ---------------------------------------------

                elif equivalencia == 4:

                    producto['stock_mostrar'] = int(
                        stock_pollo['medios_pollos'] or 0
                    )

                # ---------------------------------------------
                # CUARTO DE POLLO
                # ---------------------------------------------

                elif equivalencia == 2:

                    producto['stock_mostrar'] = int(
                        stock_pollo['cuartos_pollos'] or 0
                    )

                # ---------------------------------------------
                # OCTAVO DE POLLO
                # ---------------------------------------------

                elif equivalencia == 1:

                    producto['stock_mostrar'] = int(
                        stock_pollo['octavos_pollos'] or 0
                    )

                # ---------------------------------------------
                # EQUIVALENCIA NO VÁLIDA
                # ---------------------------------------------

                else:

                    producto['stock_mostrar'] = 0

                # ---------------------------------------------
                # SOLO MOSTRAR SI TIENE STOCK
                # ---------------------------------------------

                if producto['stock_mostrar'] > 0:

                    productos_validos.append(
                        producto
                    )

            # =================================================
            # PRODUCTOS NORMALES
            # =================================================

            else:

                producto['stock_mostrar'] = int(
                    producto['stock'] or 0
                )

                if producto['stock_mostrar'] > 0:

                    productos_validos.append(
                        producto
                    )

        # ----------------------------------------------------
        # ACTUALIZAR LISTA FINAL DE PRODUCTOS
        # ----------------------------------------------------

        productos = productos_validos

        # ----------------------------------------------------
        # 5. OBTENER PEDIDO ACTUAL DE LA MESA
        # ----------------------------------------------------

        cursor.execute("""
            SELECT
                id,
                producto_id,
                producto,
                cantidad,
                precio
            FROM pedido_mesa
            WHERE mesa_id = %s
            ORDER BY id ASC
        """, (mesa_id,))

        filas_pedido = cursor.fetchall()

        # ----------------------------------------------------
        # 6. PREPARAR PEDIDO
        # ----------------------------------------------------

        pedido_guardado = []

        for fila in filas_pedido:

            pedido_guardado.append({

                'id': int(
                    fila['producto_id']
                ),

                'nombre': fila['producto'],

                'cantidad': int(
                    fila['cantidad'] or 0
                ),

                'precio': float(
                    fila['precio'] or 0
                )

            })

        # ----------------------------------------------------
        # 7. CALCULAR TOTAL
        # ----------------------------------------------------

        total_pedido = 0.00

        for producto in pedido_guardado:

            total_pedido += (
                producto['precio']
                *
                producto['cantidad']
            )

        # ----------------------------------------------------
        # 8. MOSTRAR MESA
        # ----------------------------------------------------

        return render_template(

            'mozo_mesa.html',

            mesa=mesa,

            mesa_id=mesa_id,

            productos=productos,

            pedido_guardado=pedido_guardado,

            total_pedido=round(
                total_pedido,
                2
            )

        )

    # ========================================================
    # ERROR
    # ========================================================

    except Exception as e:

        print()
        print("==============================================")
        print("ERROR EN mozo_mesa")
        print("==============================================")
        print("Mesa ID:", mesa_id)
        print("Tipo:", type(e).__name__)
        print("Detalle:", str(e))
        print("==============================================")
        print()

        return f"""
        <!DOCTYPE html>
        <html lang="es">

        <head>

            <meta charset="UTF-8">

            <title>Error | Crocant's</title>

            <style>

                body {{
                    margin: 0;
                    padding: 40px;
                    background: #f7f7f6;
                    font-family: Arial, sans-serif;
                    color: #171717;
                }}

                .error {{
                    max-width: 800px;
                    margin: 40px auto;
                    background: white;
                    padding: 30px;
                    border-radius: 18px;
                    border: 1px solid #e8e4df;
                    box-shadow: 0 15px 40px rgba(0,0,0,.08);
                }}

                h1 {{
                    color: #f47b20;
                    margin-top: 0;
                }}

                .detalle {{
                    margin-top: 20px;
                    padding: 18px;
                    background: #fff0e2;
                    border-left: 4px solid #f47b20;
                    border-radius: 8px;
                    font-family: monospace;
                    white-space: pre-wrap;
                }}

                a {{
                    display: inline-block;
                    margin-top: 20px;
                    padding: 12px 18px;
                    background: #f47b20;
                    color: white;
                    text-decoration: none;
                    border-radius: 10px;
                    font-weight: bold;
                }}

                a:hover {{
                    background: #e9680d;
                }}

            </style>

        </head>

        <body>

            <div class="error">

                <h1>
                    No se pudo cargar la mesa
                </h1>

                <p>
                    Ocurrió un error al consultar
                    la información de la mesa.
                </p>

                <div class="detalle">
                    {type(e).__name__}: {str(e)}
                </div>

                <a href="{url_for('mozo_mesas')}">
                    ← Volver a mesas
                </a>

            </div>

        </body>

        </html>
        """

    finally:

        if cursor:
            cursor.close()

        if conexion:
            conexion.close()


# ============================================================
# GUARDAR PEDIDO DE MESA - MOZO
#
# IMPORTANTE:
# NO SE CAMBIA LA SECUENCIA ACTUAL DEL MOZO.
#
# MOZO
#   ↓
# pedido_mesa
#   ↓
# POLLOS → ordenes_cocina → POLLERO
#   ↓
# mesa ocupada
#   ↓
# cuenta / pago
#
# ÚNICO CAMBIO:
# el stock se descuenta correctamente utilizando
# el stock central de pollo en octavos.
# ============================================================


@app.route(
    '/mozo/mesa/<int:mesa_id>/guardar',
    methods=['POST']
)
def guardar_pedido_mesa(mesa_id):

    if session.get('rol') != 'mozo':

        return jsonify({
            'ok': False,
            'mensaje': 'No autorizado.'
        }), 403

    conexion = None
    cursor = None

    try:

        # ====================================================
        # 1. RECIBIR DATOS
        # ====================================================

        datos = request.get_json(
            silent=True
        ) or {}

        pedido = datos.get(
            'pedido',
            []
        )

        if not isinstance(pedido, list) or not pedido:

            return jsonify({
                'ok': False,
                'mensaje': 'No hay productos en el pedido.'
            }), 400

        # ====================================================
        # 2. CONEXIÓN
        # ====================================================

        conexion = get_connection()

        cursor = conexion.cursor(
            dictionary=True
        )

        # ====================================================
        # 3. COMPROBAR MESA
        # ====================================================

        cursor.execute("""
            SELECT
                id,
                numero,
                estado
            FROM mesas
            WHERE id = %s
            LIMIT 1
        """, (mesa_id,))

        mesa = cursor.fetchone()

        if not mesa:

            return jsonify({
                'ok': False,
                'mensaje': 'La mesa no existe.'
            }), 404

        # ====================================================
        # 4. PROCESAR CADA PRODUCTO
        # ====================================================

        for item in pedido:

            # ------------------------------------------------
            # VALIDAR ID Y CANTIDAD
            # ------------------------------------------------

            try:

                producto_id = int(
                    item.get('id')
                )

                cantidad = int(
                    item.get(
                        'cantidad',
                        1
                    )
                )

            except (TypeError, ValueError):

                return jsonify({
                    'ok': False,
                    'mensaje': 'Datos de producto no válidos.'
                }), 400

            if cantidad <= 0:
                continue

            # ------------------------------------------------
            # OBTENER PRODUCTO
            # ------------------------------------------------

            cursor.execute("""
                SELECT
                    id,
                    nombre,
                    precio,
                    stock,
                    categoria,
                    equivalencia_pollo
                FROM productos
                WHERE id = %s
                LIMIT 1
                FOR UPDATE
            """, (producto_id,))

            producto = cursor.fetchone()

            if not producto:

                return jsonify({
                    'ok': False,
                    'mensaje':
                        f'El producto con ID {producto_id} no existe.'
                }), 400

            # ------------------------------------------------
            # DATOS DEL PRODUCTO
            # ------------------------------------------------

            nombre_producto = producto['nombre']

            categoria = (
                str(
                    producto.get('categoria') or ''
                )
                .strip()
                .lower()
            )

            precio = float(
                producto['precio'] or 0
            )

            equivalencia = producto.get(
                'equivalencia_pollo'
            )

            # =================================================
            # 5. COMPROBAR Y DESCONTAR STOCK
            # =================================================
            #
            # PRODUCTO NORMAL:
            #       productos.stock
            #
            # PRODUCTO DE POLLO:
            #       stock_pollo
            #
            # EL STOCK DE POLLO UTILIZA:
            #
            #       1 pollo = 8 octavos
            #       1/2     = 4 octavos
            #       1/4     = 2 octavos
            #       1/8     = 1 octavo
            #
            # =================================================

            if equivalencia is not None:

                # ---------------------------------------------
                # PRODUCTO DE POLLO
                # ---------------------------------------------

                try:

                    equivalencia = int(
                        equivalencia
                    )

                except (TypeError, ValueError):

                    return jsonify({
                        'ok': False,
                        'mensaje':
                            f'Equivalencia de pollo inválida '
                            f'para {nombre_producto}.'
                    }), 400

                if equivalencia not in (1, 2, 4, 8):

                    return jsonify({
                        'ok': False,
                        'mensaje':
                            f'Equivalencia de pollo inválida '
                            f'para {nombre_producto}.'
                    }), 400

                # ---------------------------------------------
                # DESCONTAR DEL STOCK CENTRAL
                #
                # reservar_stock_pollo() ya utiliza la lógica
                # correcta basada en octavos.
                # ---------------------------------------------

                stock_reservado = reservar_stock_pollo(
                    cursor,
                    producto_id,
                    cantidad
                )

                if not stock_reservado:

                    return jsonify({
                        'ok': False,
                        'mensaje':
                            f'No hay suficiente stock de '
                            f'{nombre_producto}.'
                    }), 400

            else:

                # ---------------------------------------------
                # PRODUCTO NORMAL
                # ---------------------------------------------

                cursor.execute("""
                    UPDATE productos
                    SET stock = stock - %s
                    WHERE id = %s
                      AND stock >= %s
                """, (
                    cantidad,
                    producto_id,
                    cantidad
                ))

                if cursor.rowcount == 0:

                    return jsonify({
                        'ok': False,
                        'mensaje':
                            f'No hay suficiente stock de '
                            f'{nombre_producto}.'
                    }), 400

            # =================================================
            # 6. GUARDAR EN pedido_mesa
            # =================================================

            cursor.execute("""
                SELECT
                    id,
                    cantidad
                FROM pedido_mesa
                WHERE mesa_id = %s
                  AND producto_id = %s
                LIMIT 1
            """, (
                mesa_id,
                producto_id
            ))

            existente = cursor.fetchone()

            if existente:

                nueva_cantidad = (
                    int(existente['cantidad'])
                    +
                    cantidad
                )

                cursor.execute("""
                    UPDATE pedido_mesa
                    SET
                        cantidad = %s,
                        precio = %s
                    WHERE id = %s
                """, (
                    nueva_cantidad,
                    precio,
                    existente['id']
                ))

            else:

                cursor.execute("""
                    INSERT INTO pedido_mesa
                    (
                        mesa_id,
                        producto_id,
                        producto,
                        cantidad,
                        precio
                    )
                    VALUES
                    (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    )
                """, (
                    mesa_id,
                    producto_id,
                    nombre_producto,
                    cantidad,
                    precio
                ))

            # =================================================
            # 7. SOLO POLLOS → POLLERO
            #
            # ESTO SE MANTIENE IGUAL
            # =================================================

            if categoria in (
                'pollo',
                'pollos'
            ):

                cursor.execute("""
                    SELECT
                        id,
                        cantidad,
                        estado
                    FROM ordenes_cocina
                    WHERE mesa = %s
                      AND producto_id = %s
                      AND LOWER(
                          TRIM(
                              COALESCE(
                                  estado,
                                  'pendiente'
                              )
                          )
                      ) IN (
                          'pendiente',
                          'preparando'
                      )
                    LIMIT 1
                """, (
                    mesa_id,
                    producto_id
                ))

                orden_existente = cursor.fetchone()

                # ---------------------------------------------
                # SI YA EXISTE → SUMAR CANTIDAD
                # ---------------------------------------------

                if orden_existente:

                    nueva_cantidad_cocina = (
                        int(
                            orden_existente['cantidad']
                        )
                        +
                        cantidad
                    )

                    cursor.execute("""
                        UPDATE ordenes_cocina
                        SET cantidad = %s
                        WHERE id = %s
                    """, (
                        nueva_cantidad_cocina,
                        orden_existente['id']
                    ))

                # ---------------------------------------------
                # SI NO EXISTE → CREAR ORDEN
                # ---------------------------------------------

                else:

                    cursor.execute("""
                        INSERT INTO ordenes_cocina
                        (
                            mesa,
                            producto_id,
                            producto,
                            cantidad,
                            estado,
                            observacion
                        )
                        VALUES
                        (
                            %s,
                            %s,
                            %s,
                            %s,
                            'pendiente',
                            %s
                        )
                    """, (
                        mesa_id,
                        producto_id,
                        nombre_producto,
                        cantidad,
                        str(
                            item.get(
                                'observacion',
                                ''
                            ) or ''
                        )
                    ))

            # =================================================
            # 8. LOS DEMÁS PRODUCTOS NO VAN AL POLLERO
            #
            # ESTO SE MANTIENE IGUAL
            # =================================================

            else:

                print(
                    f"Producto NO enviado al pollero: "
                    f"{nombre_producto} | "
                    f"Categoría: {categoria}"
                )

        # ====================================================
        # 9. CAMBIAR ESTADO DE LA MESA
        # ====================================================

        cursor.execute("""
            UPDATE mesas
            SET estado = 'ocupada'
            WHERE id = %s
        """, (mesa_id,))

        # ====================================================
        # 10. GUARDAR TODO
        # ====================================================

        conexion.commit()

        # ====================================================
        # 11. RESPUESTA
        # ====================================================

        return jsonify({

            'ok': True,

            'mensaje':
                f'Pedido de Mesa {mesa["numero"]} '
                f'guardado correctamente.',

            'mesa_id':
                mesa_id,

            'mesa':
                mesa['numero']

        })

    # ========================================================
    # ERROR
    # ========================================================

    except Exception as e:

        if conexion:

            try:
                conexion.rollback()
            except Exception:
                pass

        print()
        print("============================================")
        print("ERROR AL GUARDAR PEDIDO DE MESA")
        print("============================================")
        print("TIPO:", type(e).__name__)
        print("DETALLE:", str(e))
        print("============================================")
        print()

        return jsonify({

            'ok': False,

            'mensaje':
                'Error al guardar el pedido: '
                + str(e)

        }), 500

    # ========================================================
    # CERRAR CONEXIÓN
    # ========================================================

    finally:

        if cursor:
            cursor.close()

        if conexion:
            conexion.close()




# ============================================================
# MOZO - PEDIDOS PARA LLEVAR
# No se asigna mesa.
# El pedido queda disponible para el cajero.
# El stock se descuenta únicamente al registrar el pedido.
# ============================================================

@app.route('/mozo/para-llevar', methods=['GET', 'POST'])
def mozo_para_llevar():

    if session.get('rol') != 'mozo':
        return redirect(url_for('login'))

    conexion = None
    cursor = None
    transaccion_iniciada = False

    try:
        conexion = get_connection()
        cursor = conexion.cursor(dictionary=True)

        error = None
        pedido_creado = None

        # ====================================================
        # REGISTRAR PEDIDO
        # ====================================================
        if request.method == 'POST':

            nombre_cliente = (
                request.form.get('nombre_cliente', '').strip()
                or 'Cliente presencial'
            )

            telefono_cliente = (
                request.form.get('telefono_cliente', '').strip()
                or None
            )

            # -----------------------------------------------
            # OBTENER PRODUCTOS VÁLIDOS
            # -----------------------------------------------
            cursor.execute("""
                SELECT id, nombre
                FROM productos
            """)

            productos_db = cursor.fetchall()

            productos_por_id = {
                int(p['id']): p
                for p in productos_db
            }

            # -----------------------------------------------
            # VALIDAR CANTIDADES
            # -----------------------------------------------
            cantidades = {}

            for producto_id, producto in productos_por_id.items():

                valor = request.form.get(
                    f'cantidad_{producto_id}',
                    '0'
                )

                try:
                    cantidad = int(valor)
                except (ValueError, TypeError):
                    raise ValueError(
                        f'Cantidad no válida para {producto["nombre"]}.'
                    )

                if cantidad < 0:
                    raise ValueError(
                        f'La cantidad de {producto["nombre"]} '
                        'no puede ser negativa.'
                    )

                if cantidad > 0:
                    cantidades[producto_id] = cantidad

            if not cantidades:
                raise ValueError(
                    'Selecciona al menos un producto para registrar el pedido.'
                )

            # -----------------------------------------------
            # INICIAR TRANSACCIÓN DE REGISTRO
            # -----------------------------------------------
            conexion.commit()
            conexion.start_transaction()
            transaccion_iniciada = True

            total = 0.0
            detalles = []

            # =================================================
            # VALIDAR PRODUCTOS Y DESCONTAR STOCK
            # =================================================
            for producto_id, cantidad in cantidades.items():

                cursor.execute("""
                    SELECT
                        id,
                        nombre,
                        precio,
                        stock,
                        categoria,
                        equivalencia_pollo
                    FROM productos
                    WHERE id = %s
                    FOR UPDATE
                """, (producto_id,))

                producto = cursor.fetchone()

                if not producto:
                    raise ValueError(
                        f'El producto con ID {producto_id} ya no existe.'
                    )

                precio = float(producto['precio'] or 0)

                if precio < 0:
                    raise ValueError(
                        f'El precio de {producto["nombre"]} no es válido.'
                    )

                subtotal = round(precio * cantidad, 2)

                # ---------------------------------------------
                # PRODUCTOS CON STOCK COMPARTIDO DE POLLO
                # ---------------------------------------------
                if producto['equivalencia_pollo'] is not None:

                    reservado = reservar_stock_pollo(
                        cursor,
                        producto_id,
                        cantidad
                    )

                    if not reservado:
                        raise ValueError(
                            f'Stock insuficiente para {producto["nombre"]}.'
                        )

                # ---------------------------------------------
                # PRODUCTOS CON STOCK NORMAL
                # ---------------------------------------------
                else:

                    cursor.execute("""
                        UPDATE productos
                        SET stock = stock - %s
                        WHERE id = %s
                          AND stock >= %s
                    """, (
                        cantidad,
                        producto_id,
                        cantidad
                    ))

                    if cursor.rowcount != 1:
                        raise ValueError(
                            f'Stock insuficiente para {producto["nombre"]}.'
                        )

                total = round(total + subtotal, 2)

                detalles.append({
                    'producto_id': producto_id,
                    'nombre': producto['nombre'],
                    'cantidad': cantidad,
                    'precio': precio,
                    'subtotal': subtotal
                })

            # =================================================
            # CREAR PEDIDO PARA LLEVAR, SIN MESA
            # =================================================
            cursor.execute("""
                INSERT INTO pedidos (
                    usuario_id,
                    cliente,
                    telefono,
                    direccion,
                    referencia,
                    metodo_pago,
                    tipo_pago_culqi,
                    total,
                    estado,
                    cliente_email,
                    tipo_pedido,
                    mesa_id,
                    latitud,
                    longitud,
                    tipo_documento_id,
                    documento
                )
                VALUES (
                    %s, %s, %s,
                    NULL, NULL, NULL, NULL,
                    %s, %s, NULL, %s,
                    NULL, NULL, NULL, NULL, NULL
                )
            """, (
                session.get('usuario_id'),
                nombre_cliente,
                telefono_cliente,
                total,
                'Recibido',
                'Llevar'
            ))

            pedido_id = cursor.lastrowid

            # =================================================
            # GUARDAR DETALLES DEL PEDIDO
            # =================================================
            for detalle in detalles:

                cursor.execute("""
                    INSERT INTO detalle_pedido (
                        pedido_id,
                        producto_id,
                        cantidad,
                        precio,
                        subtotal
                    )
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    pedido_id,
                    detalle['producto_id'],
                    detalle['cantidad'],
                    detalle['precio'],
                    detalle['subtotal']
                ))

            # =================================================
            # CONFIRMAR PEDIDO Y DESCUENTO DE STOCK
            # =================================================
            conexion.commit()
            transaccion_iniciada = False

            pedido_creado = {
                'id': pedido_id,
                'cliente': nombre_cliente,
                'telefono': telefono_cliente,
                'total': total,
                'detalles': detalles
            }

            flash(
                f'Pedido para llevar N.º {pedido_id} '
                'registrado correctamente.',
                'success'
            )

        # ====================================================
        # CARGAR CARTA DE PRODUCTOS
        # ====================================================
        cursor.execute("""
            SELECT
                id,
                nombre,
                precio,
                stock,
                categoria,
                imagen,
                equivalencia_pollo
            FROM productos
            ORDER BY categoria ASC, nombre ASC
        """)

        productos = cursor.fetchall()

        return render_template(
            'mozo_para_llevar.html',
            productos=productos,
            error=error,
            pedido_creado=pedido_creado
        )

    except ValueError as e:

        if conexion:
            try:
                conexion.rollback()
                transaccion_iniciada = False
            except Exception:
                pass

        mensaje_error = str(e)
        app.logger.warning(
            'No se pudo registrar el pedido para llevar: %s',
            mensaje_error
        )

        flash(mensaje_error, 'error')

        try:
            if cursor:
                cursor.execute("""
                    SELECT
                        id,
                        nombre,
                        precio,
                        stock,
                        categoria,
                        imagen,
                        equivalencia_pollo
                    FROM productos
                    ORDER BY categoria ASC, nombre ASC
                """)

                productos = cursor.fetchall()
            else:
                productos = []

        except Exception:
            productos = []

        return render_template(
            'mozo_para_llevar.html',
            productos=productos,
            error=mensaje_error,
            pedido_creado=None
        )

    except Exception:

        if conexion and transaccion_iniciada:
            try:
                conexion.rollback()
                transaccion_iniciada = False
            except Exception:
                pass

        app.logger.exception(
            'Error al registrar pedido para llevar'
        )

        return (
            'No se pudo registrar el pedido. '
            'Revisa la terminal de Flask para conocer el error.',
            500
        )

    finally:

        if cursor:
            try:
                cursor.close()
            except Exception:
                pass

        if conexion:
            try:
                conexion.close()
            except Exception:
                pass




           
# ============================================================
# PANEL DEL CAJERO
# ============================================================

@app.route('/panel_cajero')
def panel_cajero():

    # ========================================================
    # SEGURIDAD
    # ========================================================

    if session.get('rol') != 'cajero':
        return redirect(url_for('login'))

    conexion = None
    cursor = None

    try:

        conexion = get_connection()

        cursor = conexion.cursor(dictionary=True)

        # ====================================================
        # CUENTAS DEL MOZO
        #
        # SE MANTIENE EL FUNCIONAMIENTO ACTUAL
        # ====================================================

        cursor.execute("""
            SELECT
                id,
                mesa_id,
                mesa_numero,
                total,
                estado,
                mozo,
                fecha
            FROM cuentas_caja
            WHERE LOWER(TRIM(estado)) = 'pendiente'
            ORDER BY id DESC
        """)

        cuentas = cursor.fetchall()

        # ====================================================
        # PEDIDOS DIRECTOS DEL CLIENTE
        #
        # ESTOS PEDIDOS VIENEN DIRECTAMENTE DE pedidos.
        #
        # NO DEPENDEN DEL POLLERO.
        #
        # EJEMPLOS:
        #
        # Cliente -> Delivery
        # Cliente -> Recojo
        # Cliente -> Llevar
        # Cliente -> Mesa
        # ====================================================

        cursor.execute("""
            SELECT
                p.id,
                p.usuario_id,
                p.cliente,
                p.telefono,
                p.direccion,
                p.referencia,
                p.metodo_pago,
                p.total,
                p.estado,
                p.cliente_email,
                p.tipo_pedido,
                p.mesa_id,
                p.fecha
            FROM pedidos p
            WHERE LOWER(TRIM(p.estado)) IN (
                'recibido',
                'pendiente',
                'preparando',
                'listo'
            )
            ORDER BY p.id DESC
        """)

        pedidos_cliente = cursor.fetchall()

        # ====================================================
        # OBTENER DETALLE COMPLETO
        #
        # AQUÍ SE OBTIENEN TODOS LOS PRODUCTOS:
        #
        # - Pollos
        # - Bebidas
        # - Complementos
        # - Postres
        # - Cocteles
        # - Etc.
        #
        # NO SE FILTRA POR CATEGORÍA.
        # ====================================================

        for pedido in pedidos_cliente:

            cursor.execute("""
                SELECT
                    dp.id,
                    dp.pedido_id,
                    dp.producto_id,
                    dp.cantidad,
                    dp.precio,
                    dp.subtotal,
                    pr.nombre AS producto,
                    pr.categoria
                FROM detalle_pedido dp

                LEFT JOIN productos pr
                    ON pr.id = dp.producto_id

                WHERE dp.pedido_id = %s

                ORDER BY dp.id ASC
            """, (
                pedido['id'],
            ))

            pedido['detalles'] = cursor.fetchall()

        # ====================================================
        # VENTAS DEL DÍA
        # ====================================================

        cursor.execute("""
            SELECT
                id,
                hora,
                mesa,
                metodo_pago,
                cajero,
                total
            FROM ventas
            WHERE fecha = CURDATE()
            ORDER BY id DESC
        """)

        ventas_hoy = cursor.fetchall()

        # ====================================================
        # RESUMEN GENERAL
        # ====================================================

        cursor.execute("""
            SELECT

                COUNT(*) AS cantidad_ventas,

                COALESCE(
                    SUM(total),
                    0
                ) AS total_ventas

            FROM ventas

            WHERE fecha = CURDATE()
        """)

        resumen = cursor.fetchone() or {}

        resumen_ventas = {

            'cantidad_ventas':
                int(
                    resumen.get(
                        'cantidad_ventas'
                    ) or 0
                ),

            'total_ventas':
                float(
                    resumen.get(
                        'total_ventas'
                    ) or 0
                )
        }

        # ====================================================
        # EFECTIVO
        # ====================================================

        cursor.execute("""
            SELECT

                COALESCE(
                    SUM(total),
                    0
                ) AS total_efectivo

            FROM ventas

            WHERE fecha = CURDATE()

            AND LOWER(
                TRIM(metodo_pago)
            ) = 'efectivo'
        """)

        resumen_efectivo_db = (
            cursor.fetchone() or {}
        )

        resumen_efectivo = {

            'total_efectivo':
                float(
                    resumen_efectivo_db.get(
                        'total_efectivo'
                    ) or 0
                )
        }

        # ====================================================
        # YAPE
        # ====================================================

        cursor.execute("""
            SELECT

                COALESCE(
                    SUM(total),
                    0
                ) AS total_yape

            FROM ventas

            WHERE fecha = CURDATE()

            AND LOWER(
                TRIM(metodo_pago)
            ) = 'yape'
        """)

        resumen_yape_db = (
            cursor.fetchone() or {}
        )

        resumen_yape = {

            'total_yape':
                float(
                    resumen_yape_db.get(
                        'total_yape'
                    ) or 0
                )
        }

        # ====================================================
        # PLIN
        # ====================================================

        cursor.execute("""
            SELECT

                COALESCE(
                    SUM(total),
                    0
                ) AS total_plin

            FROM ventas

            WHERE fecha = CURDATE()

            AND LOWER(
                TRIM(metodo_pago)
            ) = 'plin'
        """)

        resumen_plin_db = (
            cursor.fetchone() or {}
        )

        resumen_plin = {

            'total_plin':
                float(
                    resumen_plin_db.get(
                        'total_plin'
                    ) or 0
                )
        }

        # ====================================================
        # TARJETA
        # ====================================================

        cursor.execute("""
            SELECT

                COALESCE(
                    SUM(total),
                    0
                ) AS total_tarjeta

            FROM ventas

            WHERE fecha = CURDATE()

            AND LOWER(
                TRIM(metodo_pago)
            ) = 'tarjeta'
        """)

        resumen_tarjeta_db = (
            cursor.fetchone() or {}
        )

        resumen_tarjeta = {

            'total_tarjeta':
                float(
                    resumen_tarjeta_db.get(
                        'total_tarjeta'
                    ) or 0
                )
        }

        # ====================================================
        # MOSTRAR PANEL
        # ====================================================

        return render_template(

            'panel_cajero.html',

            # ----------------------------------------------
            # CUENTAS DEL MOZO
            # ----------------------------------------------

            cuentas=cuentas,

            # ----------------------------------------------
            # PEDIDOS DIRECTOS DEL CLIENTE
            # ----------------------------------------------

            pedidos_cliente=pedidos_cliente,

            # ----------------------------------------------
            # VENTAS
            # ----------------------------------------------

            ventas_hoy=ventas_hoy,

            # ----------------------------------------------
            # RESÚMENES
            # ----------------------------------------------

            resumen_ventas=resumen_ventas,

            resumen_efectivo=resumen_efectivo,

            resumen_yape=resumen_yape,

            resumen_plin=resumen_plin,

            resumen_tarjeta=resumen_tarjeta
        )

    # ========================================================
    # ERROR
    # ========================================================

    except Exception as e:

        print(
            "============================================"
        )

        print(
            "❌ ERROR AL CARGAR PANEL DEL CAJERO"
        )

        print(
            "❌ TIPO:",
            type(e).__name__
        )

        print(
            "❌ DETALLE:",
            str(e)
        )

        print(
            "============================================"
        )

        return (
            "Error al cargar el panel del cajero: "
            + str(e)
        ), 500

    # ========================================================
    # CERRAR CONEXIÓN
    # ========================================================

    finally:

        if cursor:

            cursor.close()

        if conexion:

            conexion.close()


# ============================================================
# CÓDIGO PARA CAJA Y COBRO - CROCANT'S
# Incluye la consulta del pedido y las dos funciones de cobro.
# Reemplaza las versiones existentes de estas tres funciones;
# no mantengas definiciones duplicadas con los mismos nombres.
# ============================================================

@app.route(
    '/cajero/pedido/<int:pedido_id>',
    methods=['GET']
)
def cajero_ver_pedido(pedido_id):

    # ========================================================
    # SEGURIDAD
    # ========================================================

    if session.get('rol') != 'cajero':

        return jsonify({
            'ok': False,
            'mensaje': 'No autorizado.'
        }), 403

    conexion = None
    cursor = None

    try:

        # ====================================================
        # CONEXIÓN
        # ====================================================

        conexion = get_connection()

        cursor = conexion.cursor(
            dictionary=True
        )

        # ====================================================
        # OBTENER PEDIDO
        #
        # IMPORTANTE:
        #
        # AHORA TAMBIÉN OBTENEMOS:
        #
        #     tipo_pago_culqi
        #
        # Esto permite saber si Culqi fue:
        #
        #     Yape
        #     Tarjeta
        # ====================================================

        cursor.execute("""
            SELECT
                p.id,
                p.usuario_id,
                p.cliente,
                p.telefono,
                p.direccion,
                p.referencia,

                p.metodo_pago,

                p.tipo_pago_culqi,

                p.total,
                p.estado,
                p.cliente_email,
                p.tipo_pedido,
                p.mesa_id,
                p.fecha

            FROM pedidos p

            WHERE p.id = %s

            LIMIT 1
        """, (
            pedido_id,
        ))

        pedido = cursor.fetchone()

        # ====================================================
        # VERIFICAR QUE EXISTA
        # ====================================================

        if not pedido:

            return jsonify({
                'ok': False,
                'mensaje': 'Pedido no encontrado.'
            }), 404

        # ====================================================
        # OBTENER TODOS LOS PRODUCTOS
        #
        # NO FILTRAMOS POR POLLO.
        #
        # EL CAJERO NECESITA LA COMPRA COMPLETA.
        # ====================================================

        cursor.execute("""
            SELECT
                dp.id,
                dp.pedido_id,
                dp.producto_id,
                dp.cantidad,
                dp.precio,
                dp.subtotal,
                pr.nombre AS producto,
                pr.categoria

            FROM detalle_pedido dp

            LEFT JOIN productos pr
                ON pr.id = dp.producto_id

            WHERE dp.pedido_id = %s

            ORDER BY dp.id ASC
        """, (
            pedido_id,
        ))

        detalles = cursor.fetchall()

        # ====================================================
        # RESPUESTA JSON
        #
        # EL PANEL CAJERO RECIBIRÁ:
        #
        # resultado.pedido.metodo_pago
        # resultado.pedido.tipo_pago_culqi
        # resultado.detalles
        # ====================================================

        return jsonify({

            'ok': True,

            'pedido': pedido,

            'detalles': detalles
        })

    # ========================================================
    # ERROR
    # ========================================================

    except Exception as e:

        if conexion:

            try:

                conexion.rollback()

            except Exception:

                pass

        print(
            "============================================"
        )

        print(
            "❌ ERROR AL VER PEDIDO DEL CAJERO"
        )

        print(
            "❌ TIPO:",
            type(e).__name__
        )

        print(
            "❌ DETALLE:",
            str(e)
        )

        print(
            "============================================"
        )

        return jsonify({

            'ok': False,

            'mensaje':
                'Error al consultar el pedido.',

            'error':
                str(e)
        }), 500

    # ========================================================
    # CERRAR CONEXIÓN
    # ========================================================

    finally:

        if cursor:

            try:
                cursor.close()
            except Exception:
                pass

        if conexion:

            try:
                conexion.close()
            except Exception:
                pass

# FUNCIONES CORREGIDAS PARA REEMPLAZAR EN app.py
# Reemplaza las funciones actuales cajero_cobrar_cuenta y cajero_cobrar_pedido.
# No reemplaces otras rutas ni pegues este bloque al final sin retirar las versiones anteriores.

@app.route('/cajero/cobrar/<int:cuenta_id>', methods=['POST'])
def cajero_cobrar_cuenta(cuenta_id):

    if session.get('rol') != 'cajero':
        return jsonify({
            'ok': False,
            'mensaje': 'No autorizado.'
        }), 403

    conexion = None
    cursor = None

    try:
        datos = request.get_json(silent=True) or {}

        metodo_enviado = str(
            datos.get('metodo_pago') or ''
        ).strip().lower()

        metodos = {
            'efectivo': 'Efectivo',
            'yape': 'Yape',
            'plin': 'Plin',
            'transferencia': 'Transferencia',
            'tarjeta': 'Tarjeta'
        }

        if metodo_enviado not in metodos:
            return jsonify({
                'ok': False,
                'mensaje': 'Selecciona un método de pago válido.'
            }), 400

        metodo_pago = metodos[metodo_enviado]

        conexion = get_connection()
        conexion.start_transaction()
        cursor = conexion.cursor(dictionary=True)

        # Obtener y bloquear la caja abierta para vincular la venta.
        cursor.execute("""
            SELECT id
            FROM caja_sesiones
            WHERE estado = 'abierta'
            ORDER BY id DESC
            LIMIT 1
            FOR UPDATE
        """)
        caja_abierta = cursor.fetchone()

        if not caja_abierta:
            conexion.rollback()
            return jsonify({
                'ok': False,
                'mensaje': 'No hay una caja abierta. Abre caja antes de cobrar.'
            }), 409

        caja_id = caja_abierta['id']

        # Bloquear la cuenta mientras se procesa el cobro.
        cursor.execute("""
            SELECT
                id,
                mesa_id,
                mesa_numero,
                total,
                estado
            FROM cuentas_caja
            WHERE id = %s
            FOR UPDATE
        """, (cuenta_id,))

        cuenta = cursor.fetchone()

        if not cuenta:
            conexion.rollback()
            return jsonify({
                'ok': False,
                'mensaje': 'La cuenta no existe.'
            }), 404

        estado = str(cuenta['estado'] or '').strip().lower()

        if estado != 'pendiente':
            conexion.rollback()
            return jsonify({
                'ok': False,
                'mensaje': 'Esta cuenta ya fue cobrada o no está pendiente.'
            }), 400

        total = round(float(cuenta['total'] or 0), 2)

        if total <= 0:
            conexion.rollback()
            return jsonify({
                'ok': False,
                'mensaje': 'El total de la cuenta no es válido.'
            }), 400

        # Validar el dinero recibido.
        if metodo_pago == 'Efectivo':
            try:
                recibido = round(
                    float(datos.get('monto_recibido') or 0), 2
                )
            except (TypeError, ValueError):
                conexion.rollback()
                return jsonify({
                    'ok': False,
                    'mensaje': 'El monto recibido no es válido.'
                }), 400

            if recibido < total:
                conexion.rollback()
                return jsonify({
                    'ok': False,
                    'mensaje': 'El monto recibido es menor al total.'
                }), 400

            vuelto = round(recibido - total, 2)

        else:
            recibido = total
            vuelto = 0.00

        # Obtener el nombre del cajero.
        usuario_cajero = session.get('usuario', 'Cajero')

        if isinstance(usuario_cajero, dict):
            usuario_cajero = (
                usuario_cajero.get('nombres')
                or usuario_cajero.get('nombre')
                or usuario_cajero.get('correo')
                or usuario_cajero.get('email')
                or 'Cajero'
            )

        usuario_cajero = str(usuario_cajero)

        # Registrar la venta de mesa.
        cursor.execute("""
            INSERT INTO ventas (
                total,
                monto_recibido,
                vuelto,
                mesa,
                fecha,
                hora,
                metodo_pago,
                cajero,
                caja_id
            )
            VALUES (
                %s, %s, %s, %s,
                CURDATE(), CURTIME(), %s, %s, %s
            )
        """, (
            total,
            recibido,
            vuelto,
            cuenta.get('mesa_numero') or cuenta.get('mesa_id'),
            metodo_pago,
            usuario_cajero,
            caja_id
        ))

        venta_id = cursor.lastrowid

        # Marcar la cuenta como cobrada.
        cursor.execute("""
            UPDATE cuentas_caja
            SET estado = 'cobrado'
            WHERE id = %s
              AND LOWER(TRIM(estado)) = 'pendiente'
        """, (cuenta_id,))

        if cursor.rowcount != 1:
            raise RuntimeError(
                'No se pudo actualizar el estado de la cuenta.'
            )

        conexion.commit()

        return jsonify({
            'ok': True,
            'mensaje': 'Cobro registrado correctamente.',
            'venta_id': venta_id,
            'cuenta_id': cuenta_id,
            'total': total,
            'metodo_pago': metodo_pago,
            'monto_recibido': recibido,
            'vuelto': vuelto
        })

    except Exception:
        if conexion:
            try:
                conexion.rollback()
            except Exception:
                pass

        app.logger.exception(
            'Error al cobrar la cuenta de mesa %s',
            cuenta_id
        )

        return jsonify({
            'ok': False,
            'mensaje': 'No se pudo registrar el cobro de la cuenta.'
        }), 500

    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass

        if conexion:
            try:
                conexion.close()
            except Exception:
                pass





@app.route('/cajero/cobrar-pedido/<int:pedido_id>', methods=['POST'])
def cajero_cobrar_pedido(pedido_id):

    # ========================================================
    # SEGURIDAD
    # ========================================================

    if session.get('rol') != 'cajero':

        return jsonify({
            'ok': False,
            'mensaje': 'No autorizado.'
        }), 403

    conexion = None
    cursor = None

    try:

        # ====================================================
        # RECIBIR DATOS DEL PANEL
        # ====================================================

        datos = request.get_json(
            silent=True
        ) or {}

        metodo_enviado = str(
            datos.get(
                'metodo_pago',
                ''
            ) or ''
        ).strip()

        metodo_culqi_enviado = str(
            datos.get(
                'metodo_culqi',
                ''
            ) or ''
        ).strip()

        # ====================================================
        # CONEXIÓN
        # ====================================================

        conexion = get_connection()
        conexion.start_transaction()

        cursor = conexion.cursor(
            dictionary=True
        )

        # ====================================================
        # OBTENER PEDIDO
        #
        # IMPORTANTE:
        #
        # TAMBIÉN OBTENEMOS tipo_pago_culqi.
        # ====================================================

        cursor.execute("""
            SELECT
                id,
                cliente,
                telefono,
                direccion,
                referencia,

                metodo_pago,

                tipo_pago_culqi,

                total,
                estado,
                cliente_email,
                tipo_pedido,
                mesa_id,
                fecha

            FROM pedidos

            WHERE id = %s

            LIMIT 1
        """, (
            pedido_id,
        ))

        pedido = cursor.fetchone()

        # ====================================================
        # VERIFICAR PEDIDO
        # ====================================================

        if not pedido:

            return jsonify({

                'ok': False,

                'mensaje':
                    'Pedido no encontrado.'
            }), 404

        # ====================================================
        # MÉTODO DE PAGO
        # ====================================================

        metodo_cliente = str(
            pedido.get(
                'metodo_pago'
            ) or ''
        ).strip()

        metodo_normalizado = (
            metodo_cliente.lower().strip()
        )

        # ====================================================
        # MÉTODO REAL DE CULQI
        #
        # PRIORIDAD:
        #
        # 1. tipo_pago_culqi guardado en BD
        # 2. metodo_culqi enviado por el panel
        #
        # La BD tiene prioridad porque es el dato
        # que corresponde al pedido.
        # ====================================================

        tipo_pago_culqi = str(
            pedido.get(
                'tipo_pago_culqi'
            ) or ''
        ).strip()

        tipo_pago_culqi_normalizado = (
            tipo_pago_culqi.lower().strip()
        )

        # ====================================================
        # SI EL PEDIDO ES CULQI
        # ====================================================

        if metodo_normalizado == 'culqi':

            # ------------------------------------------------
            # PRIMERO USAMOS LO GUARDADO EN LA BD
            # ------------------------------------------------

            if tipo_pago_culqi_normalizado in (
                'yape',
                'tarjeta'
            ):

                metodo_normalizado = (
                    tipo_pago_culqi_normalizado
                )

            # ------------------------------------------------
            # SOLO COMO RESPALDO:
            # LO ENVIADO DESDE EL PANEL
            # ------------------------------------------------

            elif metodo_culqi_enviado.lower().strip() in (
                'yape',
                'tarjeta'
            ):

                metodo_normalizado = (
                    metodo_culqi_enviado
                    .lower()
                    .strip()
                )

            else:

                return jsonify({

                    'ok':
                        False,

                    'mensaje':
                        'El pago Culqi no tiene identificado si fue realizado con Yape o Tarjeta.'
                }), 400

        # ====================================================
        # SI NO ES CULQI, USAMOS EL MÉTODO NORMAL
        # ====================================================

        elif not metodo_normalizado:

            metodo_normalizado = (
                metodo_enviado.lower().strip()
            )

        # ====================================================
        # NORMALIZAR MÉTODO
        # ====================================================

        equivalencias = {

            'efectivo':
                'Efectivo',

            'contra entrega':
                'Efectivo',

            'yape':
                'Yape',

            'transferencia':
                'Transferencia',

            'transferencia bancaria':
                'Transferencia',

            'plin':
                'Plin',

            'tarjeta':
                'Tarjeta'
        }

        # ====================================================
        # VERIFICAR MÉTODO
        # ====================================================

        if metodo_normalizado not in equivalencias:

            return jsonify({

                'ok':
                    False,

                'mensaje':
                    'Método de pago no válido.'
            }), 400

        metodo_pago = equivalencias[
            metodo_normalizado
        ]

        # ====================================================
        # VERIFICAR SI LA VENTA YA FUE REGISTRADA
        #
        # EVITA DUPLICAR LA VENTA.
        # ====================================================

        cursor.execute("""
            SELECT
                id

            FROM ventas

            WHERE pedido_id = %s

            LIMIT 1
        """, (
            pedido_id,
        ))

        venta_existente = cursor.fetchone()

        if venta_existente:

            return jsonify({

                'ok':
                    False,

                'mensaje':
                    'Este pedido ya tiene una venta registrada.',

                'venta_id':
                    venta_existente.get(
                        'id'
                    )
            }), 400

        # ====================================================
        # ESTADO ACTUAL DEL PEDIDO
        # ====================================================

        estado_actual = str(
            pedido.get(
                'estado'
            ) or ''
        ).strip().lower()

        # ====================================================
        # CANCELADO
        # ====================================================

        if estado_actual == 'cancelado':

            return jsonify({

                'ok':
                    False,

                'mensaje':
                    'No se puede cobrar un pedido cancelado.'
            }), 400

        # ====================================================
        # PAGADO / COBRADO
        # ====================================================

        if estado_actual in (
            'pagado',
            'cobrado'
        ):

            if metodo_pago not in (
                'Yape',
                'Tarjeta'
            ):

                return jsonify({

                    'ok':
                        False,

                    'mensaje':
                        'Este pedido ya fue procesado.'
                }), 400

        # ====================================================
        # TOTAL DEL PEDIDO
        # ====================================================

        total = round(
            float(
                pedido.get(
                    'total'
                ) or 0
            ),
            2
        )

        if total <= 0:

            return jsonify({

                'ok':
                    False,

                'mensaje':
                    'El total del pedido no es válido.'
            }), 400

        # ====================================================
        # VARIABLES DE PAGO
        # ====================================================

        monto_recibido = 0.00

        vuelto = 0.00

        # ====================================================
        # EFECTIVO
        # ====================================================

        if metodo_pago == 'Efectivo':

            try:

                monto_recibido = float(
                    datos.get(
                        'monto_recibido',
                        0
                    ) or 0
                )

            except (
                TypeError,
                ValueError
            ):

                return jsonify({

                    'ok':
                        False,

                    'mensaje':
                        'Monto recibido inválido.'
                }), 400

            monto_recibido = round(
                monto_recibido,
                2
            )

            if monto_recibido < total:

                return jsonify({

                    'ok':
                        False,

                    'mensaje':
                        'El monto recibido es menor al total.',

                    'total':
                        total,

                    'monto_recibido':
                        monto_recibido

                }), 400

            vuelto = round(
                monto_recibido - total,
                2
            )

        # ====================================================
        # YAPE
        # ====================================================

        elif metodo_pago == 'Yape':

            monto_recibido = total

            vuelto = 0.00

        # ====================================================
        # TARJETA
        # ====================================================

        elif metodo_pago == 'Tarjeta':

            monto_recibido = total

            vuelto = 0.00

        # ====================================================
        # TRANSFERENCIA
        # ====================================================

        elif metodo_pago == 'Transferencia':

            monto_recibido = total

            vuelto = 0.00

        # ====================================================
        # PLIN
        # ====================================================

        elif metodo_pago == 'Plin':

            monto_recibido = total

            vuelto = 0.00

        # ====================================================
        # OBTENER CAJERO
        # ====================================================

        usuario_cajero = session.get(
            'usuario'
        )

        if isinstance(
            usuario_cajero,
            dict
        ):

            usuario_cajero = (

                usuario_cajero.get(
                    'nombres'
                )

                or usuario_cajero.get(
                    'nombre'
                )

                or usuario_cajero.get(
                    'correo'
                )

                or usuario_cajero.get(
                    'email'
                )

                or ''
            )

        if usuario_cajero is None:

            usuario_cajero = ''

        usuario_cajero = str(
            usuario_cajero
        )

        # ====================================================
        # OBTENER Y BLOQUEAR LA CAJA ABIERTA
        # ====================================================

        cursor.execute("""
            SELECT id
            FROM caja_sesiones
            WHERE estado = 'abierta'
            ORDER BY id DESC
            LIMIT 1
            FOR UPDATE
        """)
        caja_abierta = cursor.fetchone()

        if not caja_abierta:
            conexion.rollback()
            return jsonify({
                'ok': False,
                'mensaje': 'No hay una caja abierta. Abre caja antes de cobrar.'
            }), 409

        caja_id = caja_abierta['id']

        # ====================================================
        # REGISTRAR VENTA
        #
        # AQUÍ SE GUARDA EL MÉTODO REAL:
        #
        # Culqi + Yape
        #       ↓
        # ventas.metodo_pago = Yape
        #
        # Culqi + Tarjeta
        #       ↓
        # ventas.metodo_pago = Tarjeta
        # ====================================================

        cursor.execute("""
            INSERT INTO ventas
            (
                total,
                monto_recibido,
                vuelto,
                mesa,
                fecha,
                hora,
                metodo_pago,
                cajero,
                pedido_id,
                caja_id
            )
            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                CURDATE(),
                CURTIME(),
                %s,
                %s,
                %s,
                %s
            )
        """, (

            total,

            round(
                monto_recibido,
                2
            ),

            vuelto,

            pedido.get(
                'mesa_id'
            ),

            metodo_pago,

            usuario_cajero,

            pedido_id,

            caja_id
        ))

        venta_id = cursor.lastrowid

        # ====================================================
        # ACTUALIZAR PEDIDO
        #
        # IMPORTANTE:
        #
        # NO CAMBIAMOS metodo_pago DE CULQI.
        #
        # SE CONSERVA:
        #
        #     metodo_pago = Culqi
        #
        # Y:
        #
        #     tipo_pago_culqi = Yape/Tarjeta
        #
        # De esta manera queda registrada la pasarela
        # y también el medio utilizado.
        # ====================================================

        if str(
            pedido.get(
                'metodo_pago'
            ) or ''
        ).strip().lower() == 'culqi':

            cursor.execute("""
                UPDATE pedidos
                SET
                    estado = 'Pagado',
                    metodo_pago = 'Culqi',
                    tipo_pago_culqi = %s
                WHERE id = %s
            """, (

                metodo_pago,

                pedido_id
            ))

        else:

            cursor.execute("""
                UPDATE pedidos
                SET
                    estado = 'Pagado',
                    metodo_pago = %s
                WHERE id = %s
            """, (

                metodo_pago,

                pedido_id
            ))

        # ====================================================
        # CONFIRMAR
        # ====================================================

        conexion.commit()

        # ====================================================
        # RESPUESTA
        # ====================================================

        return jsonify({

            'ok':
                True,

            'mensaje':
                'Venta registrada correctamente.',

            'pedido_id':
                pedido_id,

            'venta_id':
                venta_id,

            'total':
                total,

            'metodo_pago':
                metodo_pago,

            'tipo_pago_culqi':
                (
                    metodo_pago
                    if str(
                        pedido.get(
                            'metodo_pago'
                        ) or ''
                    ).strip().lower() == 'culqi'
                    else None
                ),

            'monto_recibido':
                round(
                    monto_recibido,
                    2
                ),

            'vuelto':
                vuelto,

            'tipo_pedido':
                pedido.get(
                    'tipo_pedido'
                ),

            'cliente':
                pedido.get(
                    'cliente'
                ),

            'telefono':
                pedido.get(
                    'telefono'
                ),

            'direccion':
                pedido.get(
                    'direccion'
                ),

            'referencia':
                pedido.get(
                    'referencia'
                )
        })

    # ========================================================
    # ERROR
    # ========================================================

    except Exception as e:

        if conexion:

            try:

                conexion.rollback()

            except Exception:

                pass

        print(
            "============================================"
        )

        print(
            "❌ ERROR AL COBRAR PEDIDO DEL CLIENTE"
        )

        print(
            "❌ TIPO:",
            type(e).__name__
        )

        print(
            "❌ DETALLE:",
            str(e)
        )

        print(
            "============================================"
        )

        return jsonify({

            'ok':
                False,

            'mensaje':
                'Error al registrar la venta: '
                + str(e)
        }), 500

    # ========================================================
    # CERRAR CONEXIÓN
    # ========================================================

    finally:

        if cursor:

            try:
                cursor.close()
            except Exception:
                pass

        if conexion:

            try:
                conexion.close()
            except Exception:
                pass

# ============================================================
# ENVIAR CUENTA MOZO → CAJA
# ============================================================

@app.route(
    '/mozo/mesa/<int:mesa_id>/enviar-caja',
    methods=['POST']
)
def enviar_cuenta_caja(mesa_id):

    # --------------------------------------------------------
    # VALIDAR ROL
    # --------------------------------------------------------

    if session.get('rol') != 'mozo':
        return jsonify({
            'ok': False,
            'mensaje': 'No autorizado.'
        }), 403

    conexion = None
    cursor = None

    try:

        # ====================================================
        # RECIBIR JSON
        # ====================================================

        datos = request.get_json(silent=True)

        if not datos:
            return jsonify({
                'ok': False,
                'mensaje': 'No se recibieron datos.'
            }), 400

        pedido = datos.get('pedido', [])

        if not isinstance(pedido, list) or len(pedido) == 0:
            return jsonify({
                'ok': False,
                'mensaje': 'La cuenta no tiene productos.'
            }), 400

        # ====================================================
        # CONEXIÓN
        # ====================================================

        conexion = get_connection()

        cursor = conexion.cursor(dictionary=True)

        # ====================================================
        # BUSCAR MESA
        # ====================================================

        cursor.execute(
            """
            SELECT
                id,
                numero,
                estado
            FROM mesas
            WHERE id = %s
            LIMIT 1
            """,
            (mesa_id,)
        )

        mesa = cursor.fetchone()

        if not mesa:
            return jsonify({
                'ok': False,
                'mensaje': 'Mesa no encontrada.'
            }), 404

        # ====================================================
        # VALIDAR PRODUCTOS
        # ====================================================

        productos = []
        total = 0.00

        for item in pedido:

            if not isinstance(item, dict):
                return jsonify({
                    'ok': False,
                    'mensaje': 'Producto con formato incorrecto.'
                }), 400

            # ------------------------------------------------
            # ACEPTAR id O producto_id
            # ------------------------------------------------

            valor_id = item.get(
                'id',
                item.get('producto_id')
            )

            try:
                producto_id = int(valor_id)
            except (TypeError, ValueError):
                return jsonify({
                    'ok': False,
                    'mensaje': 'ID de producto inválido.'
                }), 400

            # ------------------------------------------------
            # CANTIDAD
            # ------------------------------------------------

            try:
                cantidad = int(
                    item.get('cantidad', 0)
                )
            except (TypeError, ValueError):
                return jsonify({
                    'ok': False,
                    'mensaje': 'Cantidad inválida.'
                }), 400

            if producto_id <= 0:
                return jsonify({
                    'ok': False,
                    'mensaje': 'ID de producto inválido.'
                }), 400

            if cantidad <= 0:
                return jsonify({
                    'ok': False,
                    'mensaje': 'Cantidad inválida.'
                }), 400

            # =================================================
            # BUSCAR PRODUCTO REAL EN BD
            # =================================================

            cursor.execute(
                """
                SELECT
                    id,
                    nombre,
                    precio
                FROM productos
                WHERE id = %s
                LIMIT 1
                """,
                (producto_id,)
            )

            producto_db = cursor.fetchone()

            if not producto_db:
                return jsonify({
                    'ok': False,
                    'mensaje':
                        f'El producto con ID {producto_id} no existe.'
                }), 400

            # =================================================
            # PRECIO REAL DE BD
            # =================================================

            precio = float(
                producto_db['precio'] or 0
            )

            subtotal = precio * cantidad

            total += subtotal

            productos.append({
                'id': producto_db['id'],
                'nombre': producto_db['nombre'],
                'cantidad': cantidad,
                'precio': precio,
                'subtotal': subtotal
            })

        # ====================================================
        # OBTENER MOZO CORRECTAMENTE
        # ====================================================

        usuario_mozo = session.get('usuario')

        if isinstance(usuario_mozo, dict):

            usuario_mozo = (
                usuario_mozo.get('nombres')
                or usuario_mozo.get('nombre')
                or usuario_mozo.get('correo')
                or usuario_mozo.get('email')
                or ''
            )

        if usuario_mozo is None:
            usuario_mozo = ''

        usuario_mozo = str(usuario_mozo)

        # ====================================================
        # CREAR CUENTA EN CAJA
        # ====================================================

        cursor.execute(
            """
            INSERT INTO cuentas_caja
            (
                mesa_id,
                mesa_numero,
                total,
                estado,
                mozo
            )
            VALUES
            (
                %s,
                %s,
                %s,
                'pendiente',
                %s
            )
            """,
            (
                mesa['id'],
                mesa['numero'],
                round(total, 2),
                usuario_mozo
            )
        )

        # ====================================================
        # ID DE CUENTA
        # ====================================================

        cuenta_id = cursor.lastrowid

        if not cuenta_id:
            raise Exception(
                'No se pudo obtener el ID de la cuenta.'
            )

        # ====================================================
        # GUARDAR DETALLE DE CUENTA
        # ====================================================

        for producto in productos:

            cursor.execute(
                """
                INSERT INTO cuentas_caja_detalle
                (
                    cuenta_id,
                    producto_id,
                    cantidad,
                    precio,
                    subtotal
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    cuenta_id,
                    producto['id'],
                    producto['cantidad'],
                    producto['precio'],
                    producto['subtotal']
                )
            )

        # ====================================================
        # LIBERAR MESA
        # ====================================================

        cursor.execute(
            """
            UPDATE mesas
            SET estado = 'libre'
            WHERE id = %s
            """,
            (mesa_id,)
        )

        # ====================================================
        # ELIMINAR PEDIDO DE LA MESA
        #
        # Ya fue enviado a caja.
        # ====================================================

        cursor.execute(
            """
            DELETE FROM pedido_mesa
            WHERE mesa_id = %s
            """,
            (mesa_id,)
        )

        # ====================================================
        # CONFIRMAR
        # ====================================================

        conexion.commit()

        # ====================================================
        # RESPUESTA
        # ====================================================

        return jsonify({
            'ok': True,
            'mensaje': 'Cuenta enviada a caja correctamente.',
            'cuenta_id': cuenta_id,
            'mesa': mesa['numero'],
            'total': round(total, 2)
        })

    # ========================================================
    # ERROR
    # ========================================================

    except Exception as e:

        if conexion:
            try:
                conexion.rollback()
            except Exception:
                pass

        print("============================================")
        print("❌ ERROR AL ENVIAR CUENTA A CAJA")
        print("❌ TIPO:", type(e).__name__)
        print("❌ DETALLE:", str(e))
        print("============================================")

        return jsonify({
            'ok': False,
            'mensaje':
                'Error al enviar la cuenta a caja: ' + str(e)
        }), 500

    # ========================================================
    # CERRAR CONEXIÓN
    # ========================================================

    finally:

        if cursor:
            cursor.close()

        if conexion:
            conexion.close()

# ============================================================
# DETALLE PEDIDO
# ============================================================

@app.route(
    '/detalle_pedido/<int:id>'
)
def detalle_pedido(id):

    if 'usuario' not in session:
        return redirect('/login')


    conexion = get_connection()

    cursor = conexion.cursor(
        dictionary=True
    )


    cursor.execute(
        """
        SELECT *
        FROM pedidos
        WHERE id=%s
        """,
        (id,)
    )


    pedido = cursor.fetchone()


    cursor.execute(
        """
        SELECT *
        FROM detalle_pedido
        WHERE pedido_id=%s
        """,
        (id,)
    )


    detalles = cursor.fetchall()


    cursor.close()
    conexion.close()


    return render_template(
        'detalle_pedido.html',
        detalles=detalles,
        pedido=pedido
    )


# ============================================================
# REINTENTAR PAGO
# ============================================================

@app.route(
    '/reintentar_pago/<int:id>'
)
def reintentar_pago(id):

    if 'usuario' not in session:
        return redirect('/login')


    conexion = get_connection()

    cursor = conexion.cursor(
        dictionary=True
    )


    cursor.execute(
        """
        SELECT *
        FROM pedidos
        WHERE id=%s
        """,
        (id,)
    )


    pedido = cursor.fetchone()


    if not pedido:

        cursor.close()
        conexion.close()

        return redirect(
            '/mis_pedidos'
        )


    cursor.execute(
        """
        UPDATE pedidos

        SET
            estado=%s,
            comprobante=NULL

        WHERE id=%s
        """,
        (
            "Recibido",
            id
        )
    )


    conexion.commit()


    cursor.close()
    conexion.close()


    return render_template(
        'pago.html'
    )

# ============================================================
# AGREGAR PRODUCTO AL CARRITO
# ============================================================

@app.route('/agregar_carrito', methods=['POST'])
def agregar_carrito():

    if 'usuario' not in session:
        return redirect('/login')

    producto_id = request.form.get('id')

    if not producto_id:
        return redirect('/carta')

    conexion = None
    cursor = None

    try:

        conexion = get_connection()
        cursor = conexion.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                id,
                nombre,
                precio,
                stock,
                equivalencia_pollo
            FROM productos
            WHERE id = %s
            FOR UPDATE
        """, (producto_id,))

        producto_db = cursor.fetchone()

        if not producto_db:
            conexion.rollback()
            return redirect('/carta')

        carrito = session.get('carrito', [])

        # ====================================================
        # INICIAR / MANTENER TIEMPO DE RESERVA
        # ====================================================

        import time

        if not carrito:
            session['carrito_inicio'] = time.time()

        # ----------------------------------------------------
        # BUSCAR CANTIDAD ACTUAL
        # ----------------------------------------------------

        cantidad_actual = 0

        for producto in carrito:
            if str(producto.get('id')) == str(producto_id):
                cantidad_actual = int(
                    producto.get('cantidad', 0)
                )
                break

        # ----------------------------------------------------
        # PRODUCTO DE POLLO
        # ----------------------------------------------------

        if producto_db.get('equivalencia_pollo') is not None:

            if not reservar_stock_pollo(
                cursor,
                producto_id,
                1
            ):
                conexion.rollback()
                return redirect('/carta')

        # ----------------------------------------------------
        # PRODUCTO NORMAL
        # ----------------------------------------------------

        else:

            stock = int(
                producto_db.get('stock') or 0
            )

            if cantidad_actual >= stock:
                conexion.rollback()
                return redirect('/carta')

        # ----------------------------------------------------
        # ACTUALIZAR CARRITO
        # ----------------------------------------------------

        encontrado = False

        for producto in carrito:

            if str(producto.get('id')) == str(producto_id):

                producto['cantidad'] = cantidad_actual + 1
                encontrado = True
                break

        if not encontrado:

            carrito.append({
                'id': str(producto_db['id']),
                'nombre': producto_db['nombre'],
                'precio': float(producto_db['precio']),
                'cantidad': 1
            })

        session['carrito'] = carrito
        session.modified = True

        conexion.commit()

        return redirect('/carta')

    except Exception as e:

        if conexion:
            conexion.rollback()

        print("❌ Error al agregar al carrito:", e)

        return redirect('/carta')

    finally:

        if cursor:
            cursor.close()

        if conexion:
            conexion.close()


# ============================================================
# VER CARRITO
# ============================================================

@app.route('/carrito', methods=['GET'])
def ver_carrito():

    if 'usuario' not in session:
        return redirect('/login')

    carrito = session.get('carrito', [])

    total = 0
    cantidad_total = 0

    for producto in carrito:

        cantidad = int(
            producto.get('cantidad', 1)
        )

        precio = float(
            producto.get('precio', 0)
        )

        producto['cantidad'] = cantidad

        total += precio * cantidad
        cantidad_total += cantidad

    session['carrito'] = carrito
    session.modified = True

    return render_template(
        'carrito.html',
        carrito=carrito,
        total=total,
        cantidad_total=cantidad_total
    )


# ============================================================
# DEVOLVER STOCK DE POLLO
# ============================================================

def devolver_stock_pollo(cursor, producto_id, cantidad=1):
    """
    Devuelve al inventario el stock reservado de pollo.

    Todo el stock físico se maneja internamente en OCTAVOS:

        1 pollo = 8 octavos
        1/2     = 4 octavos
        1/4     = 2 octavos
        1/8     = 1 octavo

    Al devolver stock:

        1 pollo -> +8 octavos
        1/2     -> +4 octavos
        1/4     -> +2 octavos
        1/8     -> +1 octavo

    Después se reconstruyen los campos de stock_pollo
    a partir del total real de octavos.

    Retorna:
        True  = devolución realizada
        False = no se pudo realizar
    """

    # --------------------------------------------------------
    # OBTENER EQUIVALENCIA DEL PRODUCTO
    # --------------------------------------------------------

    cursor.execute("""
        SELECT
            equivalencia_pollo
        FROM productos
        WHERE id = %s
        FOR UPDATE
    """, (producto_id,))

    producto = cursor.fetchone()

    if not producto:
        return False

    equivalencia = producto.get(
        'equivalencia_pollo'
    )

    # --------------------------------------------------------
    # PRODUCTO NORMAL
    # --------------------------------------------------------

    if equivalencia is None:
        return True

    try:

        equivalencia = int(
            equivalencia
        )

        cantidad = int(
            cantidad
        )

    except (
        TypeError,
        ValueError
    ):

        return False

    if cantidad <= 0:
        return False

    # --------------------------------------------------------
    # EQUIVALENCIA EN OCTAVOS
    # --------------------------------------------------------

    equivalencias = {

        8: 8,   # 1 pollo

        4: 4,   # 1/2 pollo

        2: 2,   # 1/4 pollo

        1: 1    # 1/8 pollo
    }

    if equivalencia not in equivalencias:
        return False

    # --------------------------------------------------------
    # OBTENER STOCK CENTRAL
    # --------------------------------------------------------

    cursor.execute("""
        SELECT
            pollos_enteros,
            medios_pollos,
            cuartos_pollos,
            octavos_pollos
        FROM stock_pollo
        WHERE id = 1
        FOR UPDATE
    """)

    stock = cursor.fetchone()

    if not stock:
        return False

    # --------------------------------------------------------
    # CONVERTIR STOCK ACTUAL A OCTAVOS
    # --------------------------------------------------------

    total_octavos = (

        int(
            stock.get(
                'pollos_enteros'
            ) or 0
        ) * 8

        +

        int(
            stock.get(
                'medios_pollos'
            ) or 0
        ) * 4

        +

        int(
            stock.get(
                'cuartos_pollos'
            ) or 0
        ) * 2

        +

        int(
            stock.get(
                'octavos_pollos'
            ) or 0
        )
    )

    # --------------------------------------------------------
    # CALCULAR OCTAVOS A DEVOLVER
    # --------------------------------------------------------

    octavos_a_devolver = (

        equivalencias[equivalencia]

        *

        cantidad
    )

    # --------------------------------------------------------
    # DEVOLVER AL STOCK REAL
    # --------------------------------------------------------

    nuevo_total_octavos = (

        total_octavos

        +

        octavos_a_devolver
    )

    # --------------------------------------------------------
    # RECONSTRUIR STOCK
    # --------------------------------------------------------

    nuevos_enteros = (

        nuevo_total_octavos // 8
    )

    resto = (

        nuevo_total_octavos % 8
    )

    nuevos_medios = (

        resto // 4
    )

    resto = (

        resto % 4
    )

    nuevos_cuartos = (

        resto // 2
    )

    nuevos_octavos = (

        resto % 2
    )

    # --------------------------------------------------------
    # GUARDAR STOCK RECONSTRUIDO
    # --------------------------------------------------------

    cursor.execute("""
        UPDATE stock_pollo
        SET
            pollos_enteros = %s,
            medios_pollos = %s,
            cuartos_pollos = %s,
            octavos_pollos = %s
        WHERE id = 1
    """, (
        nuevos_enteros,
        nuevos_medios,
        nuevos_cuartos,
        nuevos_octavos
    ))

    return True


# ============================================================
# DISMINUIR CANTIDAD
# ============================================================

@app.route('/disminuir_carrito/<int:index>', methods=['POST'])
def disminuir_carrito(index):

    if 'usuario' not in session:
        return redirect('/carrito')

    carrito = session.get('carrito', [])

    if index < 0 or index >= len(carrito):
        return redirect('/carrito')

    producto = carrito[index]

    producto_id = producto.get('id')

    cantidad = int(
        producto.get('cantidad', 1)
    )

    conexion = None
    cursor = None

    try:

        conexion = get_connection()

        cursor = conexion.cursor(
            dictionary=True
        )

        # ----------------------------------------------------
        # DEVOLVER UNA UNIDAD SOLO SI ES POLLO
        # ----------------------------------------------------

        devolver_stock_pollo(
            cursor,
            producto_id,
            1
        )

        # ----------------------------------------------------
        # DISMINUIR CANTIDAD DEL CARRITO
        # ----------------------------------------------------

        if cantidad > 1:

            producto['cantidad'] = (
                cantidad - 1
            )

        else:

            carrito.pop(index)

        session['carrito'] = carrito
        session.modified = True

        conexion.commit()

        return redirect('/carrito')

    except Exception as e:

        if conexion:
            conexion.rollback()

        print(
            "❌ Error al disminuir carrito:",
            e
        )

        return redirect('/carrito')

    finally:

        if cursor:
            cursor.close()

        if conexion:
            conexion.close()


# ============================================================
# AUMENTAR CANTIDAD
# ============================================================

@app.route('/aumentar_carrito/<int:index>', methods=['POST'])
def aumentar_carrito(index):

    if 'usuario' not in session:
        return redirect('/carrito')

    carrito = session.get('carrito', [])

    if index < 0 or index >= len(carrito):
        return redirect('/carrito')

    producto = carrito[index]

    producto_id = producto.get('id')

    conexion = None
    cursor = None

    try:

        conexion = get_connection()

        cursor = conexion.cursor(
            dictionary=True
        )

        cursor.execute("""
            SELECT
                id,
                stock,
                equivalencia_pollo
            FROM productos
            WHERE id = %s
            FOR UPDATE
        """, (producto_id,))

        producto_db = cursor.fetchone()

        if not producto_db:

            conexion.rollback()

            return redirect('/carrito')

        # ----------------------------------------------------
        # PRODUCTO DE POLLO
        # ----------------------------------------------------

        if producto_db.get(
            'equivalencia_pollo'
        ) is not None:

            disponible = reservar_stock_pollo(
                cursor,
                producto_id,
                1
            )

            if not disponible:

                conexion.rollback()

                return redirect('/carrito')

        # ----------------------------------------------------
        # PRODUCTO NORMAL
        # ----------------------------------------------------

        else:

            cantidad_actual = int(
                producto.get(
                    'cantidad',
                    1
                )
            )

            stock = int(
                producto_db.get(
                    'stock'
                ) or 0
            )

            if cantidad_actual >= stock:

                conexion.rollback()

                return redirect('/carrito')

        # ----------------------------------------------------
        # AUMENTAR CANTIDAD
        # ----------------------------------------------------

        producto['cantidad'] = (

            int(
                producto.get(
                    'cantidad',
                    1
                )
            )

            +

            1
        )

        session['carrito'] = carrito
        session.modified = True

        conexion.commit()

        return redirect('/carrito')

    except Exception as e:

        if conexion:
            conexion.rollback()

        print(
            "❌ Error al aumentar carrito:",
            e
        )

        return redirect('/carrito')

    finally:

        if cursor:
            cursor.close()

        if conexion:
            conexion.close()


# ============================================================
# ELIMINAR PRODUCTO DEL CARRITO
# ============================================================

@app.route(
    '/eliminar_carrito/<int:index>',
    methods=['GET', 'POST']
)
def eliminar_carrito(index):

    if 'usuario' not in session:
        return redirect('/carrito')

    carrito = session.get(
        'carrito',
        []
    )

    if index < 0 or index >= len(carrito):
        return redirect('/carrito')

    producto = carrito[index]

    producto_id = producto.get('id')

    cantidad = int(
        producto.get(
            'cantidad',
            1
        )
    )

    conexion = None
    cursor = None

    try:

        conexion = get_connection()

        cursor = conexion.cursor(
            dictionary=True
        )

        # ----------------------------------------------------
        # DEVOLVER TODAS LAS UNIDADES RESERVADAS
        # ----------------------------------------------------

        devolver_stock_pollo(
            cursor,
            producto_id,
            cantidad
        )

        # ----------------------------------------------------
        # ELIMINAR DEL CARRITO
        # ----------------------------------------------------

        carrito.pop(index)

        session['carrito'] = carrito
        session.modified = True

        conexion.commit()

        return redirect('/carrito')

    except Exception as e:

        if conexion:
            conexion.rollback()

        print(
            "❌ Error al eliminar producto "
            "del carrito:",
            e
        )

        return redirect('/carrito')

    finally:

        if cursor:
            cursor.close()

        if conexion:
            conexion.close()

# ============================================================
# BLOQUEOS
# ============================================================

def esta_bloqueado(
    correo,
    ip
):

    conexion = get_connection()

    cursor = conexion.cursor(
        dictionary=True
    )


    cursor.execute(
        """
        SELECT *

        FROM bloqueos

        WHERE ip=%s

        ORDER BY id DESC

        LIMIT 1
        """,
        (ip,)
    )


    bloqueo = cursor.fetchone()


    cursor.close()
    conexion.close()


    if not bloqueo:

        return False


    if not bloqueo[
        'bloqueado_hasta'
    ]:

        return False


    bloqueado_hasta = \
        bloqueo['bloqueado_hasta']


    if isinstance(
        bloqueado_hasta,
        str
    ):

        try:

            bloqueado_hasta = \
                datetime.strptime(
                    bloqueado_hasta,
                    '%Y-%m-%d %H:%M:%S'
                )

        except Exception:

            return False


    if (
        bloqueado_hasta
        > datetime.now()
    ):

        return True


    conexion = get_connection()

    cursor = conexion.cursor()


    cursor.execute(
        """
        DELETE FROM bloqueos
        WHERE ip=%s
        """,
        (ip,)
    )


    conexion.commit()


    cursor.close()
    conexion.close()


    return False


# ============================================================
# REGISTRAR INTENTO
# ============================================================

def registrar_intento_fallido(
    correo,
    ip
):

    conexion = get_connection()

    cursor = conexion.cursor(
        dictionary=True
    )


    cursor.execute(
        """
        SELECT *

        FROM bloqueos

        WHERE ip=%s

        ORDER BY id DESC

        LIMIT 1
        """,
        (ip,)
    )


    bloqueo = cursor.fetchone()


    if bloqueo:

        intentos = (
            int(
                bloqueo['intentos']
            )
            + 1
        )


        if intentos >= 3:

            bloqueado_hasta = (
                datetime.now()
                + timedelta(
                    minutes=15
                )
            )


            cursor.execute(
                """
                UPDATE bloqueos

                SET
                    intentos=%s,
                    bloqueado_hasta=%s

                WHERE ip=%s
                """,
                (
                    intentos,

                    bloqueado_hasta,

                    ip
                )
            )


        else:

            cursor.execute(
                """
                UPDATE bloqueos

                SET intentos=%s

                WHERE ip=%s
                """,
                (
                    intentos,
                    ip
                )
            )


    else:

        cursor.execute(
            """
            INSERT INTO bloqueos
            (
                correo,
                ip,
                intentos
            )

            VALUES
            (%s,%s,1)
            """,
            (
                correo,
                ip
            )
        )


    conexion.commit()


    cursor.close()
    conexion.close()


# ============================================================
# LIMPIAR BLOQUEO
# ============================================================

def limpiar_bloqueo(
    correo,
    ip
):

    conexion = get_connection()

    cursor = conexion.cursor()


    cursor.execute(
        """
        DELETE FROM bloqueos
        WHERE ip=%s
        """,
        (ip,)
    )


    conexion.commit()


    cursor.close()
    conexion.close()


# ============================================================
# GUARDAR DATOS DE ENVÍO
# ============================================================

@app.route(
    '/guardar_pedido',
    methods=['POST']
)
def guardar_pedido():

    session['pedido'] = {

        'nombre':
            request.form.get(
                'nombre'
            ),

        'telefono':
            request.form.get(
                'telefono'
            ),

        'direccion':
            request.form.get(
                'direccion'
            ),

        'referencia':
            request.form.get(
                'referencia'
            ),

        'documento':
            request.form.get(
                'documento'
            ),

        # TIPO DE DOCUMENTO
        # Ejemplo: dni / ruc
        'tipo':
            request.form.get(
                'tipo'
            ),

        # TIPO DE PEDIDO
        # Ejemplo: Delivery / Recojo / Mesa / Llevar
        'tipo_pedido':
            request.form.get(
                'tipo_pedido'
            ),

        # ====================================================
        # UBICACIÓN REAL DEL CLIENTE
        # Solo se utilizará para pedidos DELIVERY
        # ====================================================

        'latitud':
            request.form.get(
                'latitud'
            ),

        'longitud':
            request.form.get(
                'longitud'
            )

    }

    return redirect(
        '/pago'
    )

# ============================================================
# LOGOUT
# ============================================================

@app.route('/logout')
def logout():

    session.clear()

    return redirect(
        '/login'
    )


# ============================================================
# BOLETA
# ============================================================

@app.route(
    '/boleta_pago/<int:id>'
)
def boleta_pago(id):

    if 'usuario' not in session:
        return redirect('/login')


    conexion = get_connection()

    cursor = conexion.cursor(
        dictionary=True
    )


    cursor.execute(
        """
        SELECT *
        FROM pedidos
        WHERE id=%s
        """,
        (id,)
    )


    pedido = cursor.fetchone()


    cursor.close()
    conexion.close()


    return render_template(
        'boleta_pago.html',
        pedido=pedido
    )

# ============================================================
# REGISTRO DE NUEVO USUARIO / CLIENTE
# ============================================================

@app.route('/registro', methods=['GET', 'POST'])
def registro():

    if request.method == 'POST':

        nombres = request.form.get('nombre', '').strip()
        apellidos = request.form.get('apellido', '').strip()
        telefono = request.form.get('telefono', '').strip()
        correo = request.form.get('correo', '').strip().lower()
        clave = request.form.get('password', '').strip()

        # ====================================================
        # VALIDACIONES
        # ====================================================

        if not nombres or not apellidos or not telefono or not correo or not clave:
            flash('Todos los campos son obligatorios.', 'error')
            return redirect(url_for('registro'))

        if not telefono.isdigit() or len(telefono) != 9:
            flash('El número de celular debe tener 9 números.', 'error')
            return redirect(url_for('registro'))

        if not correo.endswith('@gmail.com'):
            flash('El correo debe ser de Gmail.', 'error')
            return redirect(url_for('registro'))

        if len(clave) < 8:
            flash('La contraseña debe tener al menos 8 caracteres.', 'error')
            return redirect(url_for('registro'))

        try:

            # =================================================
            # CONEXIÓN A ALWAYS DATA
            # =================================================

            conn = get_connection()
            cursor = conn.cursor(dictionary=True)

            # =================================================
            # VERIFICAR SI EL CORREO YA EXISTE
            # =================================================

            cursor.execute(
                "SELECT id FROM usuarios WHERE correo = %s",
                (correo,)
            )

            usuario_existente = cursor.fetchone()

            if usuario_existente:
                cursor.close()
                conn.close()

                flash('El correo ya está registrado.', 'error')
                return redirect(url_for('registro'))

            # =================================================
            # ENCRIPTAR CONTRASEÑA
            # =================================================

            clave_hash = generate_password_hash(clave)

            # =================================================
            # REGISTRAR USUARIO
            # =================================================

            cursor.execute("""
                INSERT INTO usuarios
                (nombres, apellidos, correo, clave, rol, telefono)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                nombres,
                apellidos,
                correo,
                clave_hash,
                'cliente',
                telefono
            ))

            conn.commit()

            cursor.close()
            conn.close()

            flash('Usuario registrado correctamente.', 'success')
            return redirect(url_for('login'))

        except Exception as e:

            print("==========================================")
            print("ERROR AL REGISTRAR USUARIO:")
            print(e)
            print("==========================================")

            if 'cursor' in locals():
                cursor.close()

            if 'conn' in locals():
                conn.close()

            flash('Ocurrió un error al registrar el usuario.', 'error')
            return redirect(url_for('registro'))

    return render_template('registro.html')

# ============================================================
# PERFIL DEL USUARIO
# ============================================================

@app.route('/perfil')
def perfil():

    if 'usuario' not in session:
        return redirect('/login')
    print("USUARIO ACTUAL EN SESIÓN:", session.get('usuario'))
    

    conexion = get_connection()

    cursor = conexion.cursor(dictionary=True)

    cursor.execute(
        """
        SELECT *
        FROM usuarios
        WHERE correo=%s
        """,
        (session['usuario'],)
    )

    usuario = cursor.fetchone()

    cursor.close()
    conexion.close()

    if not usuario:
        return redirect('/login')

    return render_template(
        'perfil.html',
        usuario=usuario
    )

# ============================================================
# PANEL DEL POLLERO
# ============================================================

@app.route('/pollero')
@app.route('/panel_pollero')
def panel_pollero():

    if 'usuario' not in session:
        return redirect(url_for('login'))

    if session.get('rol') != 'pollero':
        return redirect(url_for('login'))

    return render_template(
        'panel_pollero.html'
    )


# ============================================================
# PEDIDOS PARA EL POLLERO
#
# REGLA:
# UN pedido_id = UNA compra = UNA TARJETA
#
# Un pedido puede tener varios productos.
#
# Ejemplo:
#
# pedidos
# --------------------------------
# id = 52
#
# ordenes_cocina
# --------------------------------
# pedido_id | producto              | cantidad
# 52        | 1/4 Pollo + papas     | 2
# 52        | 1/2 Pollo + papas     | 1
#
# EL POLLERO VERÁ:
#
# Pedido #52
# 1/4 Pollo + papas       x2
# 1/2 Pollo + papas       x1
#
# ============================================================

@app.route(
    '/api/pollero/pedidos',
    methods=['GET']
)
def api_pollero_pedidos():

    # ========================================================
    # VALIDAR SESIÓN
    # ========================================================

    if 'usuario' not in session:

        return jsonify({

            'ok': False,

            'mensaje':
                'Sesión no iniciada.'

        }), 401


    # ========================================================
    # VALIDAR ROL
    # ========================================================

    if session.get('rol') != 'pollero':

        return jsonify({

            'ok': False,

            'mensaje':
                'No autorizado.'

        }), 403


    conexion = None
    cursor = None


    try:

        # ====================================================
        # CONEXIÓN
        # ====================================================

        conexion = get_connection()

        cursor = conexion.cursor(
            dictionary=True
        )


        # ====================================================
        # OBTENER TODAS LAS ÓRDENES DE COCINA
        #
        # IMPORTANTE:
        #
        # NO agrupamos todavía en SQL.
        #
        # Primero obtenemos cada producto.
        # Después Flask los agrupa por pedido_id.
        # ====================================================

        cursor.execute("""
            SELECT

                oc.id AS orden_cocina_id,

                oc.pedido_id,

                oc.cantidad,

                oc.producto,

                oc.estado,

                oc.observacion,

                oc.fecha,

                oc.producto_id,

                p.categoria,

                pe.id AS pedido_real_id,

                pe.cliente,

                pe.telefono,

                pe.direccion,

                pe.referencia,

                pe.tipo_pedido,

                pe.mesa_id,

                m.numero AS mesa_numero

            FROM ordenes_cocina oc

            INNER JOIN productos p
                ON p.id = oc.producto_id

            INNER JOIN pedidos pe
                ON pe.id = oc.pedido_id

            LEFT JOIN mesas m
                ON m.id = pe.mesa_id

            WHERE

                LOWER(
                    TRIM(
                        COALESCE(
                            p.categoria,
                            ''
                        )
                    )
                ) IN (
                    'pollo',
                    'pollos'
                )

            ORDER BY

                oc.pedido_id DESC,

                oc.id ASC

        """)


        registros = cursor.fetchall()


        # ====================================================
        # AGRUPAR POR PEDIDO
        #
        # IMPORTANTE:
        #
        # NO agrupamos por cliente.
        # NO agrupamos por producto.
        # NO agrupamos por orden_cocina_id.
        #
        # AGRUPAMOS ÚNICAMENTE POR:
        #
        #       pedido_id
        #
        # De esta forma:
        #
        # pedido_id 52
        # producto A
        # producto B
        # producto C
        #
        # = UNA SOLA TARJETA
        # ====================================================

        pedidos_agrupados = {}


        for registro in registros:

            pedido_id = registro.get(
                'pedido_id'
            )


            # =================================================
            # SI NO EXISTE pedido_id
            # =================================================

            if pedido_id is None:

                pedido_id = registro.get(
                    'pedido_real_id'
                )


            # =================================================
            # SI TODAVÍA NO EXISTE
            # =================================================

            if pedido_id is None:

                print(
                    '⚠️ Registro sin pedido_id:',
                    registro
                )

                continue


            # =================================================
            # NORMALIZAR TIPO DE PEDIDO
            # =================================================

            tipo_original = (
                registro.get(
                    'tipo_pedido'
                )
                or ''
            )


            tipo = str(
                tipo_original
            ).strip().lower()


            tipo_normalizado = (
                tipo
                .replace('á', 'a')
                .replace('é', 'e')
                .replace('í', 'i')
                .replace('ó', 'o')
                .replace('ú', 'u')
            )


            # =================================================
            # DETERMINAR ORIGEN
            # =================================================

            if (

                'delivery'
                in tipo_normalizado

                or

                'domicilio'
                in tipo_normalizado

                or

                'envio'
                in tipo_normalizado

            ):

                origen = 'delivery'

                nombre_origen = 'DELIVERY'


            elif (

                'mesa'
                in tipo_normalizado

                or

                'salon'
                in tipo_normalizado

            ):

                origen = 'mesa'

                nombre_origen = 'MESA'


            elif (

                'recojo'
                in tipo_normalizado

                or

                'recoger'
                in tipo_normalizado

                or

                'pickup'
                in tipo_normalizado

            ):

                # Si tiene mesa, priorizamos mesa

                if registro.get('mesa_id'):

                    origen = 'mesa'

                    nombre_origen = 'MESA'

                else:

                    origen = 'recojo'

                    nombre_origen = 'RECOJO EN LOCAL'


            elif (

                'llevar'
                in tipo_normalizado

                or

                'take away'
                in tipo_normalizado

                or

                'takeaway'
                in tipo_normalizado

            ):

                origen = 'llevar'

                nombre_origen = 'PARA LLEVAR'


            elif registro.get('mesa_id'):

                origen = 'mesa'

                nombre_origen = 'MESA'


            else:

                origen = 'pedido'

                nombre_origen = 'PEDIDO'


            # =================================================
            # DATOS DE MESA
            # =================================================

            mesa_id = registro.get(
                'mesa_id'
            )


            mesa_numero = registro.get(
                'mesa_numero'
            )


            # =================================================
            # NOMBRE A MOSTRAR
            # =================================================

            if origen == 'delivery':

                nombre_lugar = (
                    registro.get('cliente')
                    or
                    'Cliente delivery'
                )


            elif origen == 'mesa':

                if mesa_numero:

                    nombre_lugar = (
                        f'Mesa {mesa_numero}'
                    )

                elif mesa_id:

                    nombre_lugar = (
                        f'Mesa {mesa_id}'
                    )

                else:

                    nombre_lugar = 'Mesa'


            elif origen == 'recojo':

                nombre_lugar = (
                    registro.get('cliente')
                    or
                    'Cliente - Recojo'
                )


            elif origen == 'llevar':

                nombre_lugar = (
                    registro.get('cliente')
                    or
                    'Cliente - Para llevar'
                )


            else:

                nombre_lugar = (
                    registro.get('cliente')
                    or
                    'Cliente'
                )


            # =================================================
            # CREAR PEDIDO SI NO EXISTE
            # =================================================

            if pedido_id not in pedidos_agrupados:

                pedidos_agrupados[pedido_id] = {

                    'id':
                        pedido_id,

                    'pedido_id':
                        pedido_id,

                    'pedido_real_id':
                        registro.get(
                            'pedido_real_id'
                        ),

                    'cliente':
                        registro.get(
                            'cliente'
                        ) or '',

                    'telefono':
                        registro.get(
                            'telefono'
                        ) or '',

                    'direccion':
                        registro.get(
                            'direccion'
                        ) or '',

                    'referencia':
                        registro.get(
                            'referencia'
                        ) or '',

                    'tipo':
                        tipo,

                    'tipo_pedido':
                        tipo_original,

                    'origen':
                        origen,

                    'nombre_origen':
                        nombre_origen,

                    'nombre_lugar':
                        nombre_lugar,

                    'mesa':
                        mesa_numero
                        or mesa_id,

                    'mesa_numero':
                        mesa_numero,

                    'mesa_id':
                        mesa_id,

                    'fecha':
                        (
                            str(
                                registro.get(
                                    'fecha'
                                )
                            )
                            if registro.get('fecha')
                            else ''
                        ),

                    'productos': [],

                    'estado': 'pendiente'

                }


            # =================================================
            # AGREGAR PRODUCTO
            #
            # CADA FILA DE ordenes_cocina
            # SE CONVIERTE EN UN PRODUCTO DEL PEDIDO.
            # =================================================

            cantidad = registro.get(
                'cantidad'
            )


            if cantidad is None:

                cantidad = 1


            try:

                cantidad = int(
                    cantidad
                )

            except Exception:

                cantidad = 1


            producto = {

                'orden_cocina_id':
                    registro.get(
                        'orden_cocina_id'
                    ),

                'producto_id':
                    registro.get(
                        'producto_id'
                    ),

                'producto':
                    registro.get(
                        'producto'
                    )
                    or
                    'Pollo',

                'cantidad':
                    cantidad,

                'observacion':
                    registro.get(
                        'observacion'
                    )
                    or '',

                'estado':
                    str(
                        registro.get(
                            'estado'
                        )
                        or
                        'pendiente'
                    )
                    .strip()
                    .lower()

            }


            pedidos_agrupados[
                pedido_id
            ]['productos'].append(
                producto
            )


        # ====================================================
        # CALCULAR ESTADO GENERAL DE CADA PEDIDO
        #
        # REGLAS:
        #
        # SI HAY ALGÚN PENDIENTE
        #       -> PENDIENTE
        #
        # SI NO HAY PENDIENTES Y HAY PREPARANDO
        #       -> PREPARANDO
        #
        # SI TODOS ESTÁN LISTOS
        #       -> LISTO
        #
        # ====================================================

        for pedido in pedidos_agrupados.values():

            productos = pedido.get(
                'productos',
                []
            )


            if not productos:

                pedido['estado'] = 'pendiente'

                continue


            estados = [

                str(
                    producto.get(
                        'estado'
                    )
                    or
                    'pendiente'
                )
                .strip()
                .lower()

                for producto in productos

            ]


            # =================================================
            # PRIORIDAD 1: PENDIENTE
            # =================================================

            if 'pendiente' in estados:

                pedido['estado'] = 'pendiente'


            # =================================================
            # PRIORIDAD 2: PREPARANDO
            # =================================================

            elif 'preparando' in estados:

                pedido['estado'] = 'preparando'


            # =================================================
            # TODOS LISTOS
            # =================================================

            elif all(
                estado == 'listo'
                for estado in estados
            ):

                pedido['estado'] = 'listo'


            else:

                pedido['estado'] = 'pendiente'


        # ====================================================
        # CONVERTIR DICCIONARIO A LISTA
        # ====================================================

        pedidos = list(
            pedidos_agrupados.values()
        )


        # ====================================================
        # ORDENAR
        #
        # PENDIENTE
        # PREPARANDO
        # LISTO
        #
        # Y dentro de cada estado:
        # pedido más reciente primero.
        # ====================================================

        prioridad = {

            'pendiente': 1,

            'preparando': 2,

            'listo': 3

        }


        pedidos.sort(

            key=lambda pedido: (

                prioridad.get(
                    pedido.get(
                        'estado',
                        'pendiente'
                    ),
                    4
                ),

                -int(
                    pedido.get(
                        'pedido_id'
                    )
                    or
                    0
                )

            )

        )


        # ====================================================
        # MOSTRAR RESULTADO EN CONSOLA
        # ====================================================

        print(
            '============================================'
        )

        print(
            '🍗 PEDIDOS ENVIADOS AL POLLERO'
        )

        for pedido in pedidos:

            print(
                'PEDIDO:',
                pedido['pedido_id']
            )

            print(
                'CLIENTE:',
                pedido['cliente']
            )

            print(
                'ESTADO:',
                pedido['estado']
            )

            print(
                'PRODUCTOS:'
            )

            for producto in pedido['productos']:

                print(
                    '  -',
                    producto['producto'],
                    'x',
                    producto['cantidad']
                )

        print(
            '============================================'
        )


        # ====================================================
        # RESPUESTA
        # ====================================================

        return jsonify({

            'ok': True,

            'pedidos':
                pedidos

        })


    except Exception as e:

        print(
            '============================================'
        )

        print(
            '❌ ERROR API POLLERO'
        )

        print(
            'TIPO:',
            type(e).__name__
        )

        print(
            'DETALLE:',
            str(e)
        )

        print(
            '============================================'
        )


        return jsonify({

            'ok': False,

            'mensaje':
                'Error al cargar los pedidos del pollero.',

            'error':
                str(e)

        }), 500


    finally:

        if cursor:

            cursor.close()

        if conexion:

            conexion.close()


# ============================================================
# CAMBIAR ESTADO DEL PEDIDO
#
# IMPORTANTE:
#
# RECIBE pedidos.id
#
# Y ACTUALIZA TODAS LAS FILAS DE ordenes_cocina
# QUE PERTENEZCAN A ESE pedido_id.
#
# ============================================================

@app.route(
    '/api/pollero/pedido/<int:pedido_id>/estado',
    methods=['POST']
)
def api_pollero_estado(pedido_id):

    # ========================================================
    # VALIDAR SESIÓN
    # ========================================================

    if 'usuario' not in session:

        return jsonify({

            'ok': False,

            'mensaje':
                'Sesión no iniciada.'

        }), 401


    # ========================================================
    # VALIDAR ROL
    # ========================================================

    if session.get('rol') != 'pollero':

        return jsonify({

            'ok': False,

            'mensaje':
                'No autorizado.'

        }), 403


    # ========================================================
    # RECIBIR JSON
    # ========================================================

    datos = request.get_json(
        silent=True
    ) or {}


    nuevo_estado = str(
        datos.get(
            'estado',
            ''
        )
    ).strip().lower()


    # ========================================================
    # ESTADOS PERMITIDOS
    # ========================================================

    estados_validos = {

        'pendiente',

        'preparando',

        'listo'

    }


    if nuevo_estado not in estados_validos:

        return jsonify({

            'ok': False,

            'mensaje':
                'Estado no válido.'

        }), 400


    conexion = None
    cursor = None


    try:

        conexion = get_connection()

        cursor = conexion.cursor(
            dictionary=True
        )


        # ====================================================
        # VERIFICAR PEDIDO
        # ====================================================

        cursor.execute("""
            SELECT

                id,

                cliente,

                telefono,

                direccion,

                referencia,

                tipo_pedido,

                mesa_id

            FROM pedidos

            WHERE id = %s

            LIMIT 1

        """, (
            pedido_id,
        ))


        pedido = cursor.fetchone()


        if not pedido:

            return jsonify({

                'ok': False,

                'mensaje':
                    f'No existe el pedido #{pedido_id}.'

            }), 404


        # ====================================================
        # VERIFICAR PRODUCTOS DE POLLO
        # ====================================================

        cursor.execute("""
            SELECT

                oc.id,

                oc.pedido_id,

                oc.producto,

                oc.cantidad,

                oc.estado,

                oc.producto_id,

                p.categoria

            FROM ordenes_cocina oc

            INNER JOIN productos p
                ON p.id = oc.producto_id

            WHERE

                oc.pedido_id = %s

                AND LOWER(
                    TRIM( 
                        COALESCE(
                            p.categoria,
                            ''
                        )
                    )
                ) IN (
                    'pollo',
                    'pollos'
                )

            ORDER BY
                oc.id ASC

        """, (
            pedido_id,
        ))


        productos = cursor.fetchall()


        if not productos:

            return jsonify({

                'ok': False,

                'mensaje':
                    'No se encontraron productos de pollo para este pedido.'

            }), 404


        # ====================================================
        # MOSTRAR EN CONSOLA
        # ====================================================

        print(
            '--------------------------------------------'
        )

        print(
            'ACTUALIZANDO PEDIDO:',
            pedido_id
        )

        print(
            'NUEVO ESTADO:',
            nuevo_estado
        )

        print(
            'PRODUCTOS AFECTADOS:',
            len(productos)
        )


        for producto in productos:

            print(
                '-',
                producto.get('producto'),
                'x',
                producto.get('cantidad')
            )


        # ====================================================
        # ACTUALIZAR TODAS LAS FILAS
        #
        # MISMO pedido_id
        # ====================================================

        cursor.execute("""
            UPDATE ordenes_cocina oc

            INNER JOIN productos p
                ON p.id = oc.producto_id

            SET

                oc.estado = %s

            WHERE

                oc.pedido_id = %s

                AND LOWER(
                    TRIM(
                        COALESCE(
                            p.categoria,
                            ''
                        )
                    )
                ) IN (
                    'pollo',
                    'pollos'
                )

        """, (

            nuevo_estado,

            pedido_id

        ))


        filas_actualizadas = cursor.rowcount


        # ====================================================
        # GUARDAR
        # ====================================================

        conexion.commit()


        print(
            'FILAS ACTUALIZADAS:',
            filas_actualizadas
        )

        print(
            '--------------------------------------------'
        )


        # ====================================================
        # RESPUESTA
        # ====================================================

        return jsonify({

            'ok': True,

            'mensaje':
                'Estado del pedido actualizado correctamente.',

            'pedido_id':
                pedido_id,

            'estado':
                nuevo_estado,

            'productos_actualizados':
                filas_actualizadas

        })


    except Exception as e:

        if conexion:

            try:

                conexion.rollback()

            except Exception:

                pass


        print(
            '============================================'
        )

        print(
            '❌ ERROR ESTADO POLLERO'
        )

        print(
            'TIPO:',
            type(e).__name__
        )

        print(
            'DETALLE:',
            str(e)
        )

        print(
            '============================================'
        )


        return jsonify({

            'ok': False,

            'mensaje':
                'No se pudo actualizar el estado.',

            'error':
                str(e)

        }), 500


    finally:

        if cursor:

            cursor.close()

        if conexion:

            conexion.close()



# ============================================================
# PANEL DELIVERY
# ============================================================

@app.route('/panel_delivery')
def panel_delivery():

    # ========================================================
    # SEGURIDAD
    # ========================================================

    if 'usuario' not in session:
        return redirect(url_for('login'))

    if session.get('rol') != 'delivery':
        return redirect(url_for('login'))

    conexion = None
    cursor = None

    try:

        conexion = get_connection()
        cursor = conexion.cursor(dictionary=True)

        # ====================================================
        # PEDIDOS DE DELIVERY
        # ====================================================
        # Los pedidos de Delivery se leen desde:
        #
        #       delivery
        #            ↓
        #       pedidos
        #
        # Recojo local NO aparece porque no tiene registro
        # en la tabla delivery.
        #
        # NO se utiliza p.tipo porque esa columna no existe.
        # ====================================================

        cursor.execute("""
            SELECT
                p.id AS pedido_id,
                p.cliente,
                p.telefono,
                p.direccion,
                p.referencia,
                p.metodo_pago,
                p.total,

                d.estado,

                p.fecha,

                DATE_FORMAT(
                    p.fecha,
                    '%H:%i:%s'
                ) AS hora,

                p.delivery,
                p.tipo_pedido AS tipo,

                p.latitud,
                p.longitud,

                d.trabajador_id,
                d.fecha_asignacion,
                d.fecha_entrega,
                d.observacion

            FROM delivery d

            INNER JOIN pedidos p
                ON p.id = d.pedido_id

            WHERE LOWER(
                TRIM(
                    COALESCE(
                        p.tipo_pedido,
                        ''
                    )
                )
            ) = 'delivery'

            ORDER BY p.id DESC
        """)

        pedidos = cursor.fetchall()

        # ====================================================
        # PEDIDOS PENDIENTES
        # ====================================================

        cursor.execute("""
            SELECT
                COUNT(*) AS cantidad

            FROM delivery d

            INNER JOIN pedidos p
                ON p.id = d.pedido_id

            WHERE LOWER(
                TRIM(
                    COALESCE(
                        p.tipo_pedido,
                        ''
                    )
                )
            ) = 'delivery'

            AND LOWER(
                TRIM(
                    COALESCE(
                        d.estado,
                        ''
                    )
                )
            ) IN (
                'pendiente',
                'recibido',
                'preparando',
                'listo'
            )

            AND d.trabajador_id IS NULL
        """)

        resultado_pendientes = cursor.fetchone()

        cantidad_pendientes = int(
            resultado_pendientes['cantidad'] or 0
        )

        # ====================================================
        # PEDIDOS EN CAMINO
        # ====================================================

        cursor.execute("""
            SELECT
                COUNT(*) AS cantidad

            FROM delivery d

            INNER JOIN pedidos p
                ON p.id = d.pedido_id

            WHERE LOWER(
                TRIM(
                    COALESCE(
                        p.tipo_pedido,
                        ''
                    )
                )
            ) = 'delivery'

            AND LOWER(
                TRIM(
                    COALESCE(
                        d.estado,
                        ''
                    )
                )
            ) = 'en camino'
        """)

        resultado_camino = cursor.fetchone()

        cantidad_camino = int(
            resultado_camino['cantidad'] or 0
        )

        # ====================================================
        # ENTREGADOS HOY
        # ====================================================

        cursor.execute("""
            SELECT
                COUNT(*) AS cantidad

            FROM delivery d

            INNER JOIN pedidos p
                ON p.id = d.pedido_id

            WHERE LOWER(
                TRIM(
                    COALESCE(
                        p.tipo_pedido,
                        ''
                    )
                )
            ) = 'delivery'

            AND LOWER(
                TRIM(
                    COALESCE(
                        d.estado,
                        ''
                    )
                )
            ) = 'entregado'

            AND d.fecha_entrega IS NOT NULL

            AND DATE(
                d.fecha_entrega
            ) = CURDATE()
        """)

        resultado_entregados = cursor.fetchone()

        cantidad_entregados = int(
            resultado_entregados['cantidad'] or 0
        )

        # ====================================================
        # MOSTRAR PANEL
        # ====================================================

        return render_template(
            'panel_delivery.html',
            pedidos=pedidos,
            cantidad_pendientes=cantidad_pendientes,
            cantidad_camino=cantidad_camino,
            cantidad_entregados=cantidad_entregados
        )

    except Exception as e:

        print("============================================")
        print("ERROR PANEL DELIVERY")
        print("TIPO:", type(e).__name__)
        print("DETALLE:", str(e))
        print("============================================")

        return render_template(
            'panel_delivery.html',
            pedidos=[],
            cantidad_pendientes=0,
            cantidad_camino=0,
            cantidad_entregados=0
        )

    finally:

        if cursor:
            cursor.close()

        if conexion:
            conexion.close()


# ============================================================
# DELIVERY - TOMAR PEDIDO
# ============================================================

@app.route(
    '/delivery/tomar/<int:pedido_id>',
    methods=['POST']
)
def delivery_tomar_pedido(pedido_id):

    # ========================================================
    # SEGURIDAD
    # ========================================================

    if 'usuario' not in session:

        return jsonify({
            'ok': False,
            'mensaje': 'Sesión no iniciada.'
        }), 401


    if session.get('rol') != 'delivery':

        return jsonify({
            'ok': False,
            'mensaje': 'No autorizado.'
        }), 403


    conexion = None
    cursor = None


    try:

        # ====================================================
        # CONEXIÓN
        # ====================================================

        conexion = get_connection()

        cursor = conexion.cursor(
            dictionary=True
        )


        # ====================================================
        # BUSCAR PEDIDO
        # ====================================================

        cursor.execute("""
            SELECT
                p.id AS pedido_id,
                p.tipo_pedido,
                p.estado AS estado_pedido,

                d.id AS delivery_id,
                d.trabajador_id,
                d.estado AS estado_delivery

            FROM pedidos p

            INNER JOIN delivery d
                ON d.pedido_id = p.id

            WHERE p.id = %s

            LIMIT 1
        """, (
            pedido_id,
        ))


        pedido = cursor.fetchone()


        # ====================================================
        # VERIFICAR PEDIDO
        # ====================================================

        if not pedido:

            return jsonify({
                'ok': False,
                'mensaje':
                    'No se encontró el pedido.'
            }), 404


        # ====================================================
        # VERIFICAR QUE SEA DELIVERY
        # ====================================================

        tipo_pedido = str(
            pedido.get('tipo_pedido') or ''
        ).strip().lower()


        if tipo_pedido != 'delivery':

            return jsonify({
                'ok': False,
                'mensaje':
                    'Este pedido no corresponde a Delivery.'
            }), 400


        # ====================================================
        # BUSCAR TRABAJADOR DELIVERY
        # ====================================================

        cursor.execute("""
            SELECT
                t.id AS trabajador_id

            FROM trabajadores t

            INNER JOIN usuarios u
                ON u.id = t.usuario_id

            WHERE u.correo = %s

            AND LOWER(
                TRIM(
                    t.cargo
                )
            ) = 'delivery'

            LIMIT 1
        """, (
            session.get('usuario'),
        ))


        trabajador = cursor.fetchone()


        if not trabajador:

            return jsonify({
                'ok': False,
                'mensaje':
                    'No se encontró el trabajador Delivery asociado a tu usuario.'
            }), 404


        trabajador_id = trabajador[
            'trabajador_id'
        ]


        # ====================================================
        # VERIFICAR SI YA FUE TOMADO
        # ====================================================

        trabajador_actual = pedido.get(
            'trabajador_id'
        )


        if trabajador_actual is not None:

            if int(trabajador_actual) == int(
                trabajador_id
            ):

                return jsonify({
                    'ok': True,
                    'mensaje':
                        'El pedido ya está asignado a tu usuario.',
                    'pedido_id':
                        pedido_id,
                    'estado':
                        'En camino'
                })


            return jsonify({
                'ok': False,
                'mensaje':
                    'Este pedido ya fue asignado a otro Delivery.'
            }), 409


        # ====================================================
        # ASIGNAR PEDIDO AL DELIVERY
        # ====================================================

        cursor.execute("""
            UPDATE delivery

            SET
                trabajador_id = %s,
                estado = 'En camino',
                fecha_asignacion = NOW()

            WHERE pedido_id = %s

            AND trabajador_id IS NULL
        """, (
            trabajador_id,
            pedido_id
        ))


        filas = cursor.rowcount


        # ====================================================
        # VERIFICAR ACTUALIZACIÓN
        # ====================================================

        if filas == 0:

            conexion.rollback()

            return jsonify({
                'ok': False,
                'mensaje':
                    'El pedido ya fue tomado por otro Delivery.'
            }), 409


        # ====================================================
        # ACTUALIZAR PEDIDO PRINCIPAL
        # ====================================================

        cursor.execute("""
            UPDATE pedidos

            SET
                estado = 'En camino'

            WHERE id = %s
        """, (
            pedido_id,
        ))


        # ====================================================
        # GUARDAR CAMBIOS
        # ====================================================

        conexion.commit()


        print(
            "============================================"
        )

        print(
            "PEDIDO DELIVERY TOMADO"
        )

        print(
            "PEDIDO:",
            pedido_id
        )

        print(
            "TRABAJADOR:",
            trabajador_id
        )

        print(
            "ESTADO: En camino"
        )

        print(
            "============================================"
        )


        # ====================================================
        # RESPUESTA
        # ====================================================

        return jsonify({

            'ok': True,

            'mensaje':
                'Pedido tomado correctamente.',

            'pedido_id':
                pedido_id,

            'estado':
                'En camino'

        })


    except Exception as e:

        if conexion:

            try:
                conexion.rollback()
            except Exception:
                pass


        print(
            "============================================"
        )

        print(
            "ERROR DELIVERY - TOMAR PEDIDO"
        )

        print(
            "TIPO:",
            type(e).__name__
        )

        print(
            "DETALLE:",
            str(e)
        )

        print(
            "============================================"
        )


        return jsonify({

            'ok': False,

            'mensaje':
                'No se pudo tomar el pedido.',

            'error':
                str(e)

        }), 500


    finally:

        if cursor:
            cursor.close()

        if conexion:
            conexion.close()


# ============================================================
# DELIVERY - ACTUALIZAR ESTADO DEL PEDIDO
# ============================================================

@app.route(
    '/delivery/estado/<int:pedido_id>',
    methods=['POST']
)
def delivery_estado(pedido_id):

    # ========================================================
    # SEGURIDAD
    # ========================================================

    if 'usuario' not in session:

        return jsonify({
            'ok': False,
            'mensaje': 'Sesión no iniciada.'
        }), 401

    if session.get('rol') != 'delivery':

        return jsonify({
            'ok': False,
            'mensaje': 'No autorizado.'
        }), 403

    conexion = None
    cursor = None

    try:

        # ====================================================
        # RECIBIR DATOS
        # ====================================================

        datos = request.get_json(
            silent=True
        ) or {}

        nuevo_estado = str(
            datos.get('estado', '')
        ).strip()

        # ====================================================
        # ESTADOS PERMITIDOS
        # ====================================================

        estados_permitidos = {
            'Preparando',
            'Listo',
            'En camino',
            'Entregado'
        }

        if nuevo_estado not in estados_permitidos:

            return jsonify({
                'ok': False,
                'mensaje': 'Estado no válido.'
            }), 400

        # ====================================================
        # CONEXIÓN
        # ====================================================

        conexion = get_connection()

        cursor = conexion.cursor(
            dictionary=True
        )

        # ====================================================
        # BUSCAR TRABAJADOR DEL DELIVERY
        # ====================================================

        cursor.execute("""
            SELECT
                id
            FROM trabajadores
            WHERE usuario_id = (
                SELECT
                    id
                FROM usuarios
                WHERE correo = %s
                LIMIT 1
            )
            LIMIT 1
        """, (
            session.get('usuario'),
        ))

        trabajador = cursor.fetchone()

        if not trabajador:

            return jsonify({
                'ok': False,
                'mensaje':
                    'No se encontró el trabajador asociado.'
            }), 404

        trabajador_id = trabajador['id']

        # ====================================================
        # BUSCAR DELIVERY DEL PEDIDO
        # ====================================================

        cursor.execute("""
            SELECT
                id,
                pedido_id,
                trabajador_id,
                estado
            FROM delivery
            WHERE pedido_id = %s
            LIMIT 1
        """, (
            pedido_id,
        ))

        delivery_actual = cursor.fetchone()

        if not delivery_actual:

            return jsonify({
                'ok': False,
                'mensaje':
                    'No existe un registro de delivery para este pedido.'
            }), 404

        # ====================================================
        # VERIFICAR ASIGNACIÓN
        # ====================================================

        if delivery_actual.get(
            'trabajador_id'
        ) != trabajador_id:

            return jsonify({
                'ok': False,
                'mensaje':
                    'Este pedido no está asignado a tu usuario.'
            }), 403

        # ====================================================
        # ACTUALIZAR TABLA DELIVERY
        # ====================================================

        if nuevo_estado == 'Entregado':

            cursor.execute("""
                UPDATE delivery

                SET
                    estado = %s,
                    fecha_entrega = NOW()

                WHERE pedido_id = %s
                AND trabajador_id = %s
            """, (
                nuevo_estado,
                pedido_id,
                trabajador_id
            ))

        else:

            cursor.execute("""
                UPDATE delivery

                SET
                    estado = %s

                WHERE pedido_id = %s
                AND trabajador_id = %s
            """, (
                nuevo_estado,
                pedido_id,
                trabajador_id
            ))

        # ====================================================
        # ACTUALIZAR PEDIDO PRINCIPAL
        # ====================================================

        cursor.execute("""
            UPDATE pedidos

            SET
                estado = %s

            WHERE id = %s
        """, (
            nuevo_estado,
            pedido_id
        ))

        # ====================================================
        # GUARDAR
        # ====================================================

        conexion.commit()

        print("============================================")
        print("DELIVERY ESTADO ACTUALIZADO")
        print("PEDIDO:", pedido_id)
        print("TRABAJADOR:", trabajador_id)
        print("ESTADO:", nuevo_estado)
        print("============================================")

        # ====================================================
        # RESPUESTA JSON
        # ====================================================

        return jsonify({
            'ok': True,
            'mensaje':
                'Estado actualizado correctamente.',
            'pedido_id':
                pedido_id,
            'estado':
                nuevo_estado
        })

    except Exception as e:

        if conexion:

            try:
                conexion.rollback()
            except Exception:
                pass

        print("============================================")
        print("ERROR DELIVERY ACTUALIZAR ESTADO")
        print("TIPO:", type(e).__name__)
        print("DETALLE:", str(e))
        print("============================================")

        return jsonify({
            'ok': False,
            'mensaje':
                'No se pudo actualizar el estado.',
            'error':
                str(e)
        }), 500

    finally:

        if cursor:
            cursor.close()

        if conexion:
            conexion.close()



# ============================================================
# PANEL DEL ADMINISTRADOR
# ============================================================

@app.route('/panel_admin')
def panel_admin():

    if 'usuario' not in session:
        return redirect(url_for('login'))

    if session.get('rol') != 'admin':
        return redirect(url_for('login'))

    conexion = None
    cursor = None

    try:

        conexion = get_connection()

        cursor = conexion.cursor(dictionary=True)

        # ====================================================
        # 1. VENTAS TOTALES Y CANTIDAD DE VENTAS
        # ====================================================

        cursor.execute("""
            SELECT
                COUNT(*) AS cantidad_ventas,
                COALESCE(SUM(total), 0) AS ventas_totales
            FROM ventas
        """)

        resumen = cursor.fetchone()

        cantidad_ventas = int(
            resumen['cantidad_ventas'] or 0
        )

        ventas_totales = float(
            resumen['ventas_totales'] or 0
        )

        # ====================================================
        # 2. TICKET PROMEDIO
        # ====================================================

        if cantidad_ventas > 0:
            ticket_promedio = (
                ventas_totales / cantidad_ventas
            )
        else:
            ticket_promedio = 0

        # ====================================================
        # 3. VENTAS POR DÍA
        # ====================================================

        cursor.execute("""
            SELECT
                fecha,
                SUM(total) AS total
            FROM ventas
            GROUP BY fecha
            ORDER BY fecha ASC
        """)

        ventas_por_dia = cursor.fetchall()

        for venta in ventas_por_dia:

            venta['fecha'] = (
                venta['fecha'].strftime('%Y-%m-%d')
                if venta['fecha']
                else ''
            )

            venta['total'] = float(
                venta['total'] or 0
            )

        # ====================================================
        # 4. VENTAS POR MES
        # ====================================================

        cursor.execute("""
            SELECT
                DATE_FORMAT(fecha, '%Y-%m') AS mes,
                SUM(total) AS total
            FROM ventas
            GROUP BY
                YEAR(fecha),
                MONTH(fecha)
            ORDER BY
                YEAR(fecha),
                MONTH(fecha)
        """)

        ventas_por_mes = cursor.fetchall()

        for venta in ventas_por_mes:

            venta['total'] = float(
                venta['total'] or 0
            )

        # ====================================================
        # 5. VENTAS POR MÉTODO DE PAGO
        # ====================================================

        cursor.execute("""
            SELECT
                COALESCE(metodo_pago, 'Sin especificar') AS metodo_pago,
                COUNT(*) AS cantidad
            FROM ventas
            GROUP BY metodo_pago
            ORDER BY cantidad DESC
        """)

        ventas_metodo_pago = cursor.fetchall()

        for metodo in ventas_metodo_pago:

            metodo['cantidad'] = int(
                metodo['cantidad'] or 0
            )

        # ====================================================
        # 6. ENVIAR DATOS AL DASHBOARD
        # ====================================================

        return render_template(
            'dashboard.html',

            ventas_totales=ventas_totales,

            cantidad_ventas=cantidad_ventas,

            ticket_promedio=ticket_promedio,

            ventas_por_dia=ventas_por_dia,

            ventas_por_mes=ventas_por_mes,

            ventas_metodo_pago=ventas_metodo_pago
        )

    except Exception as e:

        print(
            "ERROR EN PANEL ADMIN:",
            e
        )

        return f"""
        <h2>Error en el Dashboard</h2>
        <p>{e}</p>
        """

    finally:

        if cursor:
            cursor.close()

        if conexion:
            conexion.close()

# ============================================================
# DASHBOARD DE VENTAS
# ============================================================

@app.route('/dashboard')
def dashboard():

    if 'usuario' not in session:
        return redirect('/login')

    conexion = None
    cursor = None

    try:

        conexion = get_connection()
        cursor = conexion.cursor(dictionary=True)

        # ====================================================
        # 1. VENTAS TOTALES
        # ====================================================

        cursor.execute("""
            SELECT COALESCE(SUM(total), 0) AS ventas_totales
            FROM ventas
        """)

        resultado = cursor.fetchone()

        ventas_totales = float(
            resultado['ventas_totales'] or 0
        )

        # ====================================================
        # 2. CANTIDAD DE VENTAS
        # ====================================================

        cursor.execute("""
            SELECT COUNT(*) AS cantidad_ventas
            FROM ventas
        """)

        resultado = cursor.fetchone()

        cantidad_ventas = int(
            resultado['cantidad_ventas'] or 0
        )

        # ====================================================
        # 3. TICKET PROMEDIO
        # ====================================================

        cursor.execute("""
            SELECT COALESCE(AVG(total), 0) AS ticket_promedio
            FROM ventas
        """)

        resultado = cursor.fetchone()

        ticket_promedio = float(
            resultado['ticket_promedio'] or 0
        )

        # ====================================================
        # 4. VENTAS POR MÉTODO DE PAGO
        # ====================================================

        cursor.execute("""
            SELECT
                metodo_pago,
                COUNT(*) AS cantidad,
                COALESCE(SUM(total), 0) AS total
            FROM ventas
            GROUP BY metodo_pago
            ORDER BY total DESC
        """)

        ventas_metodo_pago = cursor.fetchall()

        # ====================================================
        # 5. VENTAS POR DÍA
        # ====================================================

        cursor.execute("""
            SELECT
                fecha,
                COALESCE(SUM(total), 0) AS total
            FROM ventas
            GROUP BY fecha
            ORDER BY fecha ASC
        """)

        ventas_por_dia = cursor.fetchall()

        # ====================================================
        # 6. VENTAS POR MES
        # ====================================================

        cursor.execute("""
            SELECT
                DATE_FORMAT(fecha, '%Y-%m') AS mes,
                COALESCE(SUM(total), 0) AS total
            FROM ventas
            GROUP BY DATE_FORMAT(fecha, '%Y-%m')
            ORDER BY mes ASC
        """)

        ventas_por_mes = cursor.fetchall()

        # ====================================================
        # ENVIAR DATOS AL DASHBOARD
        # ====================================================

        return render_template(
            'dashboard.html',
            ventas_totales=ventas_totales,
            cantidad_ventas=cantidad_ventas,
            ticket_promedio=ticket_promedio,
            ventas_metodo_pago=ventas_metodo_pago,
            ventas_por_dia=ventas_por_dia,
            ventas_por_mes=ventas_por_mes
        )

    except Exception as e:

        print("❌ Error en Dashboard:", e)

        return redirect('/panel_admin')

    finally:

        if cursor:
            cursor.close()

        if conexion:
            conexion.close()


# ============================================================
# CAJA: CONSULTAR ESTADO Y ABRIR TURNO
# ============================================================

from decimal import Decimal, InvalidOperation


def obtener_caja_abierta(cursor):
    cursor.execute("""
        SELECT *
        FROM caja_sesiones
        WHERE estado = 'abierta'
        ORDER BY id DESC
        LIMIT 1
    """)
    return cursor.fetchone()



def cajero_caja_estado():
    if session.get('rol') != 'cajero':
        return jsonify({
            'ok': False,
            'mensaje': 'No autorizado.'
        }), 403

    conexion = None
    cursor = None

    try:
        conexion = get_connection()
        cursor = conexion.cursor(dictionary=True)
        caja = obtener_caja_abierta(cursor)

        if not caja:
            return jsonify({
                'ok': True,
                'abierta': False,
                'caja': None
            })

        return jsonify({
            'ok': True,
            'abierta': True,
            'caja': {
                'id': caja['id'],
                'cajero': caja['cajero'],
                'monto_apertura': float(caja['monto_apertura'] or 0),
                'fecha_apertura': (
                    caja['fecha_apertura'].isoformat(sep=' ')
                    if caja['fecha_apertura'] else None
                )
            }
        })

    except Exception:
        app.logger.exception('Error consultando el estado de caja')
        return jsonify({
            'ok': False,
            'mensaje': 'No se pudo consultar la caja.'
        }), 500

    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()

# ============================================================
# CAJERO CAJA ABRIR
# ============================================================

@app.route('/cajero/caja/abrir', methods=['POST'])
def cajero_caja_abrir():
    if session.get('rol') != 'cajero':
        return jsonify({
            'ok': False,
            'mensaje': 'No autorizado.'
        }), 403

    datos = request.get_json(silent=True) or {}

    try:
        monto = Decimal(str(datos.get('monto_apertura', '0')))

        if not monto.is_finite() or monto < 0:
            raise InvalidOperation

        monto = monto.quantize(Decimal('0.01'))

    except (InvalidOperation, ValueError, TypeError):
        return jsonify({
            'ok': False,
            'mensaje': 'Ingresa un monto de apertura válido.'
        }), 400

    cajero = str(session.get('usuario') or '').strip()

    if not cajero:
        return jsonify({
            'ok': False,
            'mensaje': 'No se pudo identificar al cajero.'
        }), 400

    conexion = None
    cursor = None

    try:
        conexion = get_connection()
        conexion.start_transaction()
        cursor = conexion.cursor(dictionary=True)

        cursor.execute("""
            SELECT id
            FROM caja_sesiones
            WHERE estado = 'abierta'
            LIMIT 1
            FOR UPDATE
        """)

        existente = cursor.fetchone()

        if existente:
            conexion.rollback()
            return jsonify({
                'ok': False,
                'mensaje': 'Ya existe una caja abierta.'
            }), 409

        cursor.execute("""
            INSERT INTO caja_sesiones
                (cajero, monto_apertura, estado, fecha_apertura)
            VALUES (%s, %s, 'abierta', NOW())
        """, (cajero, monto))

        caja_id = cursor.lastrowid
        conexion.commit()

        return jsonify({
            'ok': True,
            'mensaje': 'Caja abierta correctamente.',
            'caja_id': caja_id,
            'monto_apertura': float(monto)
        })

    except Exception:
        if conexion:
            conexion.rollback()

        app.logger.exception('Error abriendo caja')

        return jsonify({
            'ok': False,
            'mensaje': 'No se pudo abrir la caja.'
        }), 500

    finally:
        if cursor:
            cursor.close()

        if conexion:
            conexion.close()


# ============================================================
# CAJERO ESTADO
# ============================================================

@app.route('/cajero/caja/estado', methods=['GET'])
def cajero_caja_estado():
    if session.get('rol') != 'cajero':
        return jsonify({
            'ok': False,
            'mensaje': 'No autorizado.'
        }), 403

    conexion = None
    cursor = None

    try:
        conexion = get_connection()
        cursor = conexion.cursor(dictionary=True)

        caja = obtener_caja_abierta(cursor)

        if not caja:
            return jsonify({
                'ok': True,
                'abierta': False,
                'monto_apertura': 0,
                'ventas_dia': 0,
                'total_gastos': 0,
                'resultado': 0
            })

        apertura = Decimal(str(caja.get('monto_apertura') or 0))

        # Sumar las ventas registradas desde la apertura.
        cursor.execute("""
            SELECT COALESCE(SUM(total), 0) AS total
            FROM ventas
            WHERE TIMESTAMP(fecha, hora) >= %s
        """, (caja['fecha_apertura'],))

        fila_ventas = cursor.fetchone() or {}
        ventas = Decimal(str(fila_ventas.get('total') or 0))

        # Sumar los gastos asociados a esta caja.
        cursor.execute("""
            SELECT COALESCE(SUM(monto), 0) AS total
            FROM caja_gastos
            WHERE caja_id = %s
        """, (caja['id'],))

        fila_gastos = cursor.fetchone() or {}
        gastos = Decimal(str(fila_gastos.get('total') or 0))

        # Fórmula solicitada.
        resultado = ventas - (apertura + gastos)

        return jsonify({
            'ok': True,
            'abierta': True,
            'monto_apertura': float(apertura),
            'ventas_dia': float(ventas),
            'total_gastos': float(gastos),
            'resultado': float(resultado)
        })

    except Exception:
        app.logger.exception('Error consultando el estado de caja')
        return jsonify({
            'ok': False,
            'mensaje': 'Error al consultar la caja. Revisa la consola de Flask.'
        }), 500

    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


# ============================================================
# CAJA GASTOS
# ============================================================

@app.route('/cajero/caja/gastos', methods=['POST'])
def cajero_caja_gastos():
    if session.get('rol') != 'cajero':
        return jsonify({
            'ok': False,
            'mensaje': 'No autorizado.'
        }), 403

    datos = request.get_json(silent=True) or {}

    try:
        monto = Decimal(str(datos.get('monto', '0')))

        if not monto.is_finite() or monto <= 0:
            raise InvalidOperation

        monto = monto.quantize(Decimal('0.01'))

    except (InvalidOperation, ValueError, TypeError):
        return jsonify({
            'ok': False,
            'mensaje': 'Ingresa un monto de gasto válido.'
        }), 400

    motivo = str(datos.get('motivo') or '').strip()

    if not motivo:
        return jsonify({
            'ok': False,
            'mensaje': 'Ingresa el motivo del gasto.'
        }), 400

    conexion = None
    cursor = None

    try:
        conexion = get_connection()
        conexion.start_transaction()
        cursor = conexion.cursor(dictionary=True)

        caja = obtener_caja_abierta(cursor)

        if not caja:
            conexion.rollback()
            return jsonify({
                'ok': False,
                'mensaje': 'No hay una caja abierta.'
            }), 409

        cursor.execute("""
            INSERT INTO caja_gastos
                (caja_id, cajero, monto, categoria, detalle, fecha_registro)
            VALUES (%s, %s, %s, %s, %s, NOW())
        """, (
            caja['id'],
            str(session.get('usuario') or 'Cajero'),
            monto,
            motivo,
            motivo
        ))

        conexion.commit()

        return jsonify({
            'ok': True,
            'mensaje': 'Gasto registrado correctamente.',
            'monto': float(monto)
        })

    except Exception:
        if conexion:
            conexion.rollback()

        app.logger.exception('Error registrando gasto')

        return jsonify({
            'ok': False,
            'mensaje': 'No se pudo registrar el gasto.'
        }), 500

    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


# ============================================================
# EJECUTAR
# ============================================================

if __name__ == '__main__':

    app.run(
        debug=True,
        port=5002
    )