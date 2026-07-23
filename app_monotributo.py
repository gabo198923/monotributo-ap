"""
App web para Monotributistas (con login y control de suscripción)
--------------------------------------------------------------------
Sube CSVs de Ventas y Compras, suma los totales, calcula la diferencia
(balance) y muestra un gráfico comparativo.

Requiere login (usuario/contraseña) y una suscripción activa, controlados
desde un Google Sheet. Ver GUIA_CONFIGURACION.md para el setup completo.

Cómo ejecutarla localmente:
    1. Instalar dependencias:
       pip install -r requirements.txt --break-system-packages
    2. Configurar .streamlit/secrets.toml (ver guía)
    3. Ejecutar:
       streamlit run app_monotributo.py
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import re
import unicodedata
import streamlit_authenticator as stauth

st.set_page_config(page_title="Monotributo - Ventas vs Compras", page_icon="🧾", layout="centered")

st.markdown(
    """
    <style>
    h1 { letter-spacing: 0.5px; }
    [data-testid="stMetric"] {
        background-color: #17181A;
        border: 0.5px solid #2C2C2A;
        border-radius: 8px;
        padding: 12px 16px;
    }
    [data-testid="stMetricLabel"] { color: #888780; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ===========================================================
# AUTENTICACIÓN Y CONTROL DE SUSCRIPCIÓN (Streamlit Secrets)
# ===========================================================
# Todo se configura desde "Settings > Secrets" en Streamlit Cloud.
# Ahí pegás un bloque por cada cliente (ver GUIA_CONFIGURACION.md).
# No hace falta Google Cloud, ni Sheets, ni ninguna cuenta externa.

def cargar_credenciales():
    """Arma el diccionario de usuarios que necesita streamlit-authenticator
    a partir de los Secrets. Cada cliente es un bloque [[clientes]] en Secrets."""
    credenciales = {"usernames": {}}
    for cliente in st.secrets.get("clientes", []):
        credenciales["usernames"][cliente["username"]] = {
            "name": cliente["name"],
            "email": cliente.get("email", ""),
            "password": cliente["password"],  # ya hasheada (ver generar_password.py)
        }
    return credenciales


def suscripcion_activa(username):
    """Chequea el flag 'activo' del cliente en Secrets."""
    for cliente in st.secrets.get("clientes", []):
        if cliente["username"] == username:
            return bool(cliente.get("activo", False))
    return False


credenciales = cargar_credenciales()

if not credenciales["usernames"]:
    st.error(
        "Todavía no hay ningún cliente configurado en Secrets. "
        "Seguí la GUIA_CONFIGURACION.md para agregar el primero."
    )
    st.stop()

authenticator = stauth.Authenticate(
    credenciales,
    cookie_name="monotributo_app",
    key="clave-secreta-cambiar-por-una-propia",
    cookie_expiry_days=30,
)

authenticator.login()

estado = st.session_state.get("authentication_status")

if estado is False:
    st.error("Usuario o contraseña incorrectos.")
    st.stop()
elif estado is None:
    st.info("Ingresá tu usuario y contraseña para acceder.")
    st.stop()
elif estado:
    usuario_actual = st.session_state["username"]
    if not suscripcion_activa(usuario_actual):
        st.error(
            "Tu suscripción no está activa. "
            "Contactanos para renovarla y volver a acceder."
        )
        authenticator.logout("Cerrar sesión", "main")
        st.stop()

    with st.sidebar:
        st.write(f"👋 Hola, **{st.session_state['name']}**")
        authenticator.logout("Cerrar sesión", "sidebar")

# ===========================================================
# A PARTIR DE ACÁ: la app normal, solo la ve un usuario logueado y activo
# ===========================================================

st.title("🧾 Ventas vs Compras - Monotributo")
st.write(
    "Subí tus CSV de **Mis Comprobantes / AFIP** (ventas y compras). La app detecta "
    "automáticamente las columnas de fecha, denominación e importe, suma los totales, "
    "calcula la diferencia y te muestra gráficos comparativos."
)


# ---------------------------------------------------------
# Funciones auxiliares
# ---------------------------------------------------------

def _normalizar(texto):
    """Saca tildes/ñ y pasa a minúsculas, para comparar nombres de columnas sin
    depender de la codificación (los CSV de AFIP suelen venir en Latin-1)."""
    texto = str(texto)
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return texto.lower().strip()


def cargar_csv(archivo, etiqueta):
    """Lee un CSV de AFIP: separador ';', decimal ',', probando distintas
    codificaciones (los exports de AFIP suelen venir en Latin-1 / cp1252)."""
    for encoding in ("utf-8-sig", "latin-1", "cp1252"):
        try:
            archivo.seek(0)
            df = pd.read_csv(archivo, sep=";", decimal=",", thousands=None, encoding=encoding)
            # Si el separador ; no funcionó (quedó todo en 1 columna), probamos coma
            if df.shape[1] == 1:
                archivo.seek(0)
                df = pd.read_csv(archivo, encoding=encoding)
            df.columns = [str(c).strip() for c in df.columns]
            return df
        except Exception:
            continue
    st.error(f"No se pudo leer el archivo de {etiqueta}. Verificá que sea un CSV válido.")
    return None


def encontrar_columna(df, palabras_clave, excluir=None):
    """Busca la columna cuyo nombre normalizado matchea mejor alguna de las
    palabras clave. Prioriza coincidencias más específicas (más largas) antes
    que las genéricas, para no confundir por ejemplo 'Imp. Total' con
    'Imp. Neto Gravado Total'."""
    excluir = excluir or []
    palabras_ordenadas = sorted(palabras_clave, key=len, reverse=True)

    candidatos = []
    for col in df.columns:
        col_norm = _normalizar(col)
        if any(_normalizar(p) in col_norm for p in excluir):
            continue
        for i, palabra in enumerate(palabras_ordenadas):
            if _normalizar(palabra) in col_norm:
                candidatos.append((i, len(col_norm), col))
                break

    if not candidatos:
        return None
    candidatos.sort(key=lambda x: (x[0], x[1]))
    return candidatos[0][2]


def elegir_columna(df, etiqueta, key, palabras_clave, excluir=None, tipo="numero"):
    """Detecta automáticamente la columna y permite corregirla manualmente."""
    sugerida = encontrar_columna(df, palabras_clave, excluir=excluir)
    columnas = df.columns.tolist()

    if sugerida and sugerida in columnas:
        idx = columnas.index(sugerida)
    else:
        idx = 0

    return st.selectbox(
        f"Columna de {etiqueta}",
        columnas,
        index=idx,
        key=key,
    )


def a_numero(serie):
    """Convierte una columna de importes (formato AR: 1.234,56) a float,
    por si el parseo automático de pandas no lo resolvió."""
    if pd.api.types.is_numeric_dtype(serie):
        return serie
    return (
        serie.astype(str)
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
        .str.extract(r"(-?\d+\.?\d*)")[0]
        .astype(float)
    )


# ---------------------------------------------------------
# Categorías de Monotributo (montos oficiales AFIP/ARCA)
# ---------------------------------------------------------
# Vigentes desde el 01/02/2026 hasta el 31/07/2026. ARCA actualiza estos
# topes 2 veces al año (febrero y agosto) según la inflación. Cuando
# publiquen la tabla nueva, actualizá estos valores acá.
CATEGORIAS_MONOTRIBUTO = [
    ("A", 10_277_988.13),
    ("B", 15_058_447.71),
    ("C", 21_113_696.52),
    ("D", 26_212_853.42),
    ("E", 30_833_964.37),
    ("F", 38_642_048.36),
    ("G", 46_211_109.37),
    ("H", 70_113_407.33),
    ("I", 78_479_211.62),
    ("J", 89_872_640.30),
    ("K", 108_357_084.05),
]


def calcular_categoria(facturacion_anual):
    """Dada la facturación de los últimos 12 meses, devuelve:
    (letra de categoría actual, tope de esa categoría, letra de la
    siguiente categoría o None si ya está en la K, cuánto falta para
    llegar al tope actual, % de la categoría actual ya utilizado)."""
    for i, (letra, tope) in enumerate(CATEGORIAS_MONOTRIBUTO):
        if facturacion_anual <= tope:
            tope_anterior = CATEGORIAS_MONOTRIBUTO[i - 1][1] if i > 0 else 0
            siguiente = CATEGORIAS_MONOTRIBUTO[i + 1][0] if i + 1 < len(CATEGORIAS_MONOTRIBUTO) else None
            rango_categoria = tope - tope_anterior
            usado_en_categoria = facturacion_anual - tope_anterior
            porcentaje = usado_en_categoria / rango_categoria if rango_categoria > 0 else 1.0
            falta_para_tope = tope - facturacion_anual
            return letra, tope, siguiente, falta_para_tope, porcentaje
    # Superó la categoría K: queda excluido del monotributo
    return "EXCLUIDO", None, None, None, 1.0


# ---------------------------------------------------------
# Pestañas
# ---------------------------------------------------------

tab_comparacion, tab_recategorizacion = st.tabs(["📊 Ventas vs Compras", "📈 Recategorización"])

with tab_comparacion:
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("📈 Ventas")
        archivo_ventas = st.file_uploader("Subí el CSV de ventas", type="csv", key="ventas")

    with col2:
        st.subheader("📉 Compras")
        archivo_compras = st.file_uploader("Subí el CSV de compras", type="csv", key="compras")

    # ---------------------------------------------------------
    # Procesamiento
    # ---------------------------------------------------------

    if archivo_ventas and archivo_compras:
        df_ventas = cargar_csv(archivo_ventas, "ventas")
        df_compras = cargar_csv(archivo_compras, "compras")

        if df_ventas is not None and df_compras is not None:
            st.divider()
            st.subheader("Columnas detectadas")
            st.caption("Se detectan automáticamente. Corregilas acá si hiciera falta.")

            # Palabras clave para el monto total: probamos primero las variantes más
            # específicas que usa AFIP ("Importe Total", "Imp. Total") y excluimos
            # columnas de sub-totales (neto gravado, IVA, exento, etc.) para no
            # confundirlas con el total real del comprobante.
            claves_monto = ["importe total", "imp. total", "imp total"]
            excluir_monto = ["neto", "gravado", "iva", "exent", "no gravado", "tributo"]

            claves_denom = ["denominacion receptor", "denominacion comprador", "denominacion vendedor", "denominacion", "receptor", "cliente", "proveedor"]

            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Ventas**")
                col_fecha_v = elegir_columna(df_ventas, "fecha", "fecha_v", ["fecha"])
                col_monto_v = elegir_columna(df_ventas, "importe/monto", "monto_v", claves_monto, excluir=excluir_monto)
                col_denom_v = elegir_columna(df_ventas, "denominación", "denom_v", claves_denom)
            with c2:
                st.markdown("**Compras**")
                col_fecha_c = elegir_columna(df_compras, "fecha", "fecha_c", ["fecha"])
                col_monto_c = elegir_columna(df_compras, "importe/monto", "monto_c", claves_monto, excluir=excluir_monto)
                col_denom_c = elegir_columna(df_compras, "denominación", "denom_c", claves_denom)

            if col_monto_v and col_monto_c:
                df_ventas["_monto"] = a_numero(df_ventas[col_monto_v])
                df_compras["_monto"] = a_numero(df_compras[col_monto_c])
                df_ventas["_fecha"] = pd.to_datetime(df_ventas[col_fecha_v], errors="coerce")
                df_compras["_fecha"] = pd.to_datetime(df_compras[col_fecha_c], errors="coerce")

                total_ventas = df_ventas["_monto"].sum()
                total_compras = df_compras["_monto"].sum()
                balance = total_ventas - total_compras

                st.divider()
                st.subheader("Resultados")

                m1, m2, m3 = st.columns(3)
                m1.metric("Total Ventas", f"${total_ventas:,.2f}")
                m2.metric("Total Compras", f"${total_compras:,.2f}")
                m3.metric(
                    "Balance",
                    f"${balance:,.2f}",
                    delta=("Ganancia" if balance >= 0 else "Pérdida"),
                )

                # ---------------------------------------------------------
                # Gráfico 1: comparación de totales
                # ---------------------------------------------------------
                fig_totales = go.Figure(
                    data=[
                        go.Bar(
                            x=["Ventas", "Compras"],
                            y=[total_ventas, total_compras],
                            marker_color=["#5DCAA5", "#F0997B"],
                            text=[f"${total_ventas:,.2f}", f"${total_compras:,.2f}"],
                            textposition="auto",
                        )
                    ]
                )
                fig_totales.update_layout(
                    title="Comparación de Totales: Ventas vs Compras",
                    yaxis_title="Monto ($)",
                    showlegend=False,
                    height=420,
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#D8D6CC"),
                )
                st.plotly_chart(fig_totales, use_container_width=True)

                # ---------------------------------------------------------
                # Gráfico 2: evolución diaria (si hay fechas válidas)
                # ---------------------------------------------------------
                if df_ventas["_fecha"].notna().any() and df_compras["_fecha"].notna().any():
                    serie_v = df_ventas.dropna(subset=["_fecha"]).groupby("_fecha")["_monto"].sum()
                    serie_c = df_compras.dropna(subset=["_fecha"]).groupby("_fecha")["_monto"].sum()

                    fig_evol = go.Figure()
                    fig_evol.add_trace(go.Scatter(x=serie_v.index, y=serie_v.values, mode="lines+markers", name="Ventas", line=dict(color="#5DCAA5")))
                    fig_evol.add_trace(go.Scatter(x=serie_c.index, y=serie_c.values, mode="lines+markers", name="Compras", line=dict(color="#F0997B")))
                    fig_evol.update_layout(
                        title="Evolución diaria: Ventas vs Compras",
                        yaxis_title="Monto ($)",
                        xaxis_title="Fecha",
                        height=420,
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        font=dict(color="#D8D6CC"),
                    )
                    st.plotly_chart(fig_evol, use_container_width=True)

                # ---------------------------------------------------------
                # Top clientes / proveedores por monto
                # ---------------------------------------------------------
                if col_denom_v or col_denom_c:
                    t1, t2 = st.columns(2)
                    if col_denom_v:
                        with t1:
                            st.markdown("**Top 10 clientes (ventas)**")
                            top_v = df_ventas.groupby(col_denom_v)["_monto"].sum().sort_values(ascending=False).head(10)
                            st.dataframe(top_v.map(lambda x: f"${x:,.2f}"), use_container_width=True)
                    if col_denom_c:
                        with t2:
                            st.markdown("**Top 10 proveedores (compras)**")
                            top_c = df_compras.groupby(col_denom_c)["_monto"].sum().sort_values(ascending=False).head(10)
                            st.dataframe(top_c.map(lambda x: f"${x:,.2f}"), use_container_width=True)

                # Detalle de las tablas cargadas (opcional, colapsable)
                with st.expander("Ver datos de ventas"):
                    st.dataframe(df_ventas.drop(columns=["_monto", "_fecha"]), use_container_width=True)

                with st.expander("Ver datos de compras"):
                    st.dataframe(df_compras.drop(columns=["_monto", "_fecha"]), use_container_width=True)

    else:
        st.info("Subí ambos archivos CSV para ver los resultados.")


with tab_recategorizacion:
    st.subheader("📈 Recategorización de Monotributo")
    st.write(
        "Subí el CSV de ventas de **los últimos 12 meses** (podés exportarlo desde "
        "Mis Comprobantes / AFIP con ese rango de fechas). Calculamos tu categoría "
        "actual y cuánto te falta facturar para pasar a la siguiente."
    )
    st.caption(
        "Montos vigentes desde 01/02/2026 hasta 31/07/2026 (ARCA actualiza la tabla "
        "cada 6 meses). Si estás viendo esto después de esa fecha, avisale a quien "
        "armó la app para que actualice los valores."
    )

    archivo_recat = st.file_uploader(
        "Subí el CSV de ventas del último año", type="csv", key="recat"
    )

    st.markdown("**¿No tenés el CSV a mano? Ingresá el total facturado manualmente:**")
    facturacion_manual = st.number_input(
        "Facturación de los últimos 12 meses ($)",
        min_value=0.0,
        step=1000.0,
        format="%.2f",
    )

    facturacion_anual = None

    if archivo_recat:
        df_recat = cargar_csv(archivo_recat, "recategorización")
        if df_recat is not None:
            claves_monto = ["importe total", "imp. total", "imp total"]
            excluir_monto = ["neto", "gravado", "iva", "exent", "no gravado", "tributo"]
            col_monto_recat = elegir_columna(
                df_recat, "importe/monto", "monto_recat", claves_monto, excluir=excluir_monto
            )
            if col_monto_recat:
                df_recat["_monto"] = a_numero(df_recat[col_monto_recat])
                facturacion_anual = df_recat["_monto"].sum()
    elif facturacion_manual > 0:
        facturacion_anual = facturacion_manual

    if facturacion_anual:
        letra, tope, siguiente, falta, porcentaje = calcular_categoria(facturacion_anual)

        st.divider()

        if letra == "EXCLUIDO":
            st.error(
                f"Facturación de ${facturacion_anual:,.2f} supera el tope máximo del "
                f"monotributo (categoría K: ${CATEGORIAS_MONOTRIBUTO[-1][1]:,.2f}). "
                "Corresponde evaluar el pasaje a Responsable Inscripto."
            )
        else:
            m1, m2, m3 = st.columns(3)
            m1.metric("Facturación (12 meses)", f"${facturacion_anual:,.2f}")
            m2.metric("Categoría actual", letra)
            if siguiente:
                m3.metric("Falta para la próxima", f"${falta:,.2f}")
            else:
                m3.metric("Categoría máxima", "Ya estás en la K")

            st.write(f"**Progreso dentro de la categoría {letra}** (tope: ${tope:,.2f})")
            st.progress(min(porcentaje, 1.0))

            if siguiente:
                st.caption(
                    f"Con ${falta:,.2f} más de facturación en los últimos 12 meses, "
                    f"pasarías a la categoría {siguiente}."
                )
            else:
                st.caption(
                    "Estás en la categoría más alta del monotributo. Si superás este "
                    "tope, corresponde pasar a Responsable Inscripto."
                )

            with st.expander("Ver tabla completa de categorías"):
                tabla_cats = pd.DataFrame(
                    CATEGORIAS_MONOTRIBUTO, columns=["Categoría", "Tope de facturación anual"]
                )
                tabla_cats["Tope de facturación anual"] = tabla_cats["Tope de facturación anual"].map(
                    lambda x: f"${x:,.2f}"
                )
                st.dataframe(tabla_cats, use_container_width=True, hide_index=True)
    else:
        st.info("Subí el CSV o ingresá el monto manualmente para calcular la categoría.")
