"""Pruebas del motor contra la base v2 y casos limite de negocio."""
from pathlib import Path
import math
from io import BytesIO

import pandas as pd
import pytest

from motor import (
    cargar_base, oficinas_cercanas, simular, auditoria_base, excel_resultados,
    distancia_haversine, pesos_cercania, redistribuir_enteros,
)

BASE_FILE = Path(__file__).resolve().parents[1] / "BASE CIERRE OFICINAS - BASE TOTAL_v2.csv"


@pytest.fixture(scope="module")
def base():
    return cargar_base(BASE_FILE)


def test_base_real(base):
    assert base.shape == (252, 17)
    assert base["COD OF"].is_unique
    assert base["TIEMPO ATENCION PLT"].notna().sum() == 251
    issues = auditoria_base(base)
    assert len(issues) == 1
    assert issues.iloc[0]["codigo"] == "121"
    assert issues.iloc[0]["canal"] == "Plataforma"


def test_formulas_coinciden_base(base):
    for tickets, tm, est, hu in [
        ("TKT VNT", "TIEMPO ATENCION VNT", "EST AU", "HORAS UTILES VNT"),
        ("TKT PLT", "TIEMPO ATENCION PLT", "EST EBP", "HORAS UTILES PLT"),
    ]:
        calculadas = base[tickets] * base[tm] / (60 * 24 * base[est])
        diff = abs(calculadas - base[hu]).dropna()
        assert len(diff) in (251, 252)
        assert (diff <= .1).all()


def test_distancia_y_ceros():
    assert distancia_haversine(-12, -77, -12, -77) == 0
    assert distancia_haversine(-12, -77, -12.01, -77) > 1
    assert pesos_cercania([1, 2, 3]) == pytest.approx([6 / 11, 3 / 11, 2 / 11])
    assert pesos_cercania([0, 0, 1]) == [0.5, 0.5, 0]
    assert sum(redistribuir_enteros(11, [1 / 3] * 3,
                                    ["1", "2", "3"], [1, 2, 3])) == 11


def test_cierre_real_y_conservacion(base):
    candidates = oficinas_cercanas(base, "13", 3)
    assert len(candidates) >= 5
    select = candidates["COD OF"].head(5).tolist()
    r = simular(base, "13", candidates, select)
    assert len(r.resumen) == 5
    assert len(r.detalle) == 10
    for _, bal in r.balance.iterrows():
        assert bal["tickets_planeados"] == bal["tickets_origen"]
        assert bal["tickets_asignados"] == bal["tickets_planeados"]
        assert r.detalle.loc[r.detalle["canal"] == bal["canal"], "tickets_recibidos"].sum() == bal["tickets_planeados"]
    assert r.resumen["participacion_pct"].sum() == pytest.approx(100)
    assert r.detalle["adicional_cierre"].notna().all()


def test_pct_editables_e_independientes(base):
    candidates = oficinas_cercanas(base, "13", 3)
    r = simular(base, "13", candidates, candidates["COD OF"].head(3).tolist(),
                porcentaje_ventanilla=70, porcentaje_plataforma=30)
    bal = r.balance.set_index("canal")
    assert bal.loc["Ventanilla", "tickets_asignados"] == math.floor(3223 * .70 + .5)
    assert bal.loc["Plataforma", "tickets_asignados"] == 390
    assert bal.loc["Ventanilla", "tickets_no_redistribuidos"] == 3223 - bal.loc["Ventanilla", "tickets_asignados"]


def test_incompleta_y_conserva_canal_valido(base):
    c = oficinas_cercanas(base, "317", 3)
    assert "121" in c["COD OF"].tolist()
    r = simular(base, "317", c, ["121"])
    det = r.detalle.set_index("canal")
    assert pd.isna(det.loc["Plataforma", "adicional_cierre"])
    assert det.loc["Plataforma", "dictamen"] == "Sin datos suficientes"
    assert pd.notna(det.loc["Ventanilla", "horas_futuras"])
    assert "no concluyente" in r.resumen.iloc[0]["dictamen_general"] or "parcial" in r.resumen.iloc[0]["dictamen_general"]


def test_deficit_previo_y_adicional_no_duplicado(base):
    b = base.copy()
    rcv = b["COD OF"] == "150"
    b.loc[rcv, "EST AU"] = 1
    b.loc[rcv, "TKT VNT"] = 2400
    b.loc[rcv, "TIEMPO ATENCION VNT"] = 6
    c = oficinas_cercanas(b, "13", 3)
    r = simular(b, "13", c, ["150"], porcentaje_plataforma=0)
    v = r.detalle.query("canal == 'Ventanilla'").iloc[0]
    # 2400 tickets -> 2 personas, actual 1: deficit previo = 1.
    assert v["deficit_previo"] == 1
    # 2400 + 3223 = 5623; con 6 minutos/ticket y 7h max -> 4 personas.
    assert v["estructura_requerida_futura"] == 4
    assert v["deficit_total"] == 3
    assert v["adicional_cierre"] == 2


def test_exactamente_umbral_no_crece(base):
    b = base.copy()
    mask = b["COD OF"] == "150"
    b.loc[mask, "TKT VNT"] = 1680
    b.loc[mask, "TIEMPO ATENCION VNT"] = 6.0
    b.loc[mask, "EST AU"] = 1
    c = oficinas_cercanas(b, "13", 3)
    r = simular(b, "13", c, ["150"], porcentaje_ventanilla=0, porcentaje_plataforma=0)
    v = r.detalle.query("canal == 'Ventanilla'").iloc[0]
    assert v["horas_futuras"] == pytest.approx(7)
    assert v["estructura_requerida_futura"] == 1
    assert v["adicional_cierre"] == 0


def test_cierre_sin_receptoras_y_excel(base):
    c = oficinas_cercanas(base, "10", 3)
    assert c.empty
    r = simular(base, "10", c, [])
    assert r.resumen.empty and r.detalle.empty
    assert r.balance["tickets_sin_receptora"].sum() == r.balance["tickets_planeados"].sum()
    datos = excel_resultados(r, auditoria_base(base), c)
    wb = pd.ExcelFile(BytesIO(datos))
    assert "Detalle canales" in wb.sheet_names
    assert "Balance tickets" in wb.sheet_names
    assert "Calidad base" in wb.sheet_names


def test_no_se_admiten_receptoras_fuera_radio(base):
    c = oficinas_cercanas(base, "10", 3)
    with pytest.raises(ValueError):
        simular(base, "10", c, ["13"])
