"""
App web para Monotributistas
------------------------------
Sube CSVs de Ventas y Compras, suma los totales, calcula la diferencia
(balance) y muestra un gráfico comparativo.

Cómo ejecutarla:
    1. Instalar dependencias:
       pip install streamlit pandas plotly --break-system-packages
    2. Ejecutar:
       streamlit run app_monotributo.py
    3. Se abre automáticamente en el navegador (http://localhost:8501)
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import re
import unicodedata

st.set_page_config(page_title="Monotributo - Ventas vs Compras", page_icon="🧾", layout="centered")

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
# Carga de archivos
# ---------------------------------------------------------

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
                        marker_color=["#2ecc71", "#e74c3c"],
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
            )
            st.plotly_chart(fig_totales, use_container_width=True)

            # ---------------------------------------------------------
            # Gráfico 2: evolución diaria (si hay fechas válidas)
            # ---------------------------------------------------------
            if df_ventas["_fecha"].notna().any() and df_compras["_fecha"].notna().any():
                serie_v = df_ventas.dropna(subset=["_fecha"]).groupby("_fecha")["_monto"].sum()
                serie_c = df_compras.dropna(subset=["_fecha"]).groupby("_fecha")["_monto"].sum()

                fig_evol = go.Figure()
                fig_evol.add_trace(go.Scatter(x=serie_v.index, y=serie_v.values, mode="lines+markers", name="Ventas", line=dict(color="#2ecc71")))
                fig_evol.add_trace(go.Scatter(x=serie_c.index, y=serie_c.values, mode="lines+markers", name="Compras", line=dict(color="#e74c3c")))
                fig_evol.update_layout(
                    title="Evolución diaria: Ventas vs Compras",
                    yaxis_title="Monto ($)",
                    xaxis_title="Fecha",
                    height=420,
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
