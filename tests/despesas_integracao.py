"""Testes de integracao da API de despesas com persistencia SQLite."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_repository
from app.main import app
from app.repositories.despesas_repository import DespesaRepository


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    return tmp_path / "despesas-integration.sqlite3"


@pytest.fixture
def repository(database_path: Path) -> DespesaRepository:
    return DespesaRepository(str(database_path))


@pytest.fixture
def client(repository: DespesaRepository) -> Iterator[TestClient]:
    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_repository] = lambda: repository
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)


def buscar_registro(database_path: Path, despesa_id: str) -> tuple[str, ...] | None:
    with sqlite3.connect(database_path) as connection:
        return connection.execute(
            """
            SELECT id, descricao, valor, data_transacao, categoria, forma_pagamento
            FROM despesas
            WHERE id = ?
            """,
            (despesa_id,),
        ).fetchone()


def criar_despesa(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/despesas",
        json={
            "descricao": "Conta de energia",
            "valor": "125.50",
            "data_transacao": "2026-05-10",
            "categoria": "moradia",
            "forma_pagamento": "debito",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_criar_despesa_persiste_no_banco(
    client: TestClient, database_path: Path
) -> None:
    response = client.post(
        "/despesas",
        json={
            "descricao": "Conta de energia",
            "valor": "125.50",
            "data_transacao": "2026-05-10",
            "categoria": "moradia",
            "forma_pagamento": "debito",
        },
    )

    assert response.status_code == 201
    despesa = response.json()
    assert despesa["id"]
    assert despesa["descricao"] == "Conta de energia"
    assert despesa["valor"] == "125.50"
    assert buscar_registro(database_path, despesa["id"]) == (
        despesa["id"],
        "Conta de energia",
        "125.50",
        "2026-05-10",
        "moradia",
        "debito",
    )


def test_consultar_despesa_por_id_retorna_registro_persistido(
    client: TestClient, database_path: Path
) -> None:
    criada = criar_despesa(client)

    response = client.get(f"/despesas/{criada['id']}")

    assert response.status_code == 200
    assert response.json() == criada
    assert buscar_registro(database_path, criada["id"]) is not None


def test_listar_despesas_retorna_registros_persistidos(client: TestClient) -> None:
    primeira = criar_despesa(client)
    segunda_response = client.post(
        "/despesas",
        json={
            "descricao": "Passagem de onibus",
            "valor": "8.60",
            "data_transacao": "2026-05-11",
            "categoria": "transporte",
            "forma_pagamento": "credito",
        },
    )
    assert segunda_response.status_code == 201

    response = client.get("/despesas", params={"categoria": "moradia"})

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [primeira["id"]]
    assert body["paginacao"] == {
        "total": 1,
        "limit": 50,
        "offset": 0,
        "has_next": False,
    }


def test_atualizar_despesa_persiste_alteracoes_e_preserva_demais_campos(
    client: TestClient, database_path: Path
) -> None:
    criada = criar_despesa(client)

    response = client.put(
        f"/despesas/{criada['id']}",
        json={"valor": "150.75", "categoria": "contas"},
    )

    assert response.status_code == 200
    assert response.json() == {
        **criada,
        "valor": "150.75",
        "categoria": "contas",
    }
    assert buscar_registro(database_path, criada["id"]) == (
        criada["id"],
        "Conta de energia",
        "150.75",
        "2026-05-10",
        "contas",
        "debito",
    )


def test_excluir_despesa_remove_registro_do_banco(
    client: TestClient, database_path: Path
) -> None:
    criada = criar_despesa(client)
    assert buscar_registro(database_path, criada["id"]) is not None

    response = client.delete(f"/despesas/{criada['id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert buscar_registro(database_path, criada["id"]) is None
    assert client.get(f"/despesas/{criada['id']}").status_code == 404


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/despesas/00000000-0000-0000-0000-000000000001", None),
        (
            "put",
            "/despesas/00000000-0000-0000-0000-000000000001",
            {"descricao": "Despesa alterada"},
        ),
        ("delete", "/despesas/00000000-0000-0000-0000-000000000001", None),
    ],
)
def test_operacoes_em_despesa_inexistente_retorna_404(
    client: TestClient, method: str, path: str, payload: dict[str, str] | None
) -> None:
    response = client.request(method, path, json=payload)

    assert response.status_code == 404


def test_criacao_com_payload_invalido_retorna_422_sem_persistir(
    client: TestClient, repository: DespesaRepository, database_path: Path
) -> None:
    response = client.post(
        "/despesas",
        json={
            "descricao": "X",
            "valor": "-1.00",
            "data_transacao": "2026-05-10",
            "categoria": "moradia",
            "forma_pagamento": "dinheiro",
        },
    )

    assert response.status_code == 422
    assert repository.count() == 0
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM despesas").fetchone() == (0,)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"descricao": "Aluguel", "valor": "100.00", "data_transacao": "2026-05-10", "categoria": "moradia"},
        {"descricao": "  ", "valor": "100.00", "data_transacao": "2026-05-10", "categoria": "moradia", "forma_pagamento": "debito"},
        {"descricao": "AB", "valor": "100.00", "data_transacao": "2026-05-10", "categoria": "moradia", "forma_pagamento": "debito"},
        {"descricao": "Conta de luz", "valor": "abc", "data_transacao": "2026-05-10", "categoria": "moradia", "forma_pagamento": "debito"},
        {"descricao": "Conta de luz", "valor": "100.001", "data_transacao": "2026-05-10", "categoria": "moradia", "forma_pagamento": "debito"},
        {"descricao": "Conta de luz", "valor": "100.00", "data_transacao": "data-invalida", "categoria": "moradia", "forma_pagamento": "debito"},
        {"descricao": "Conta de luz", "valor": "100.00", "data_transacao": "2026-05-10", "categoria": "moradia", "forma_pagamento": "dinheiro"},
        {"descricao": "Conta de luz", "valor": "100.00", "data_transacao": "2026-05-10", "categoria": "moradia", "forma_pagamento": "debito", "campo_extra": True},
    ],
)
def test_criacao_com_campos_ausentes_ou_invalidos_retorna_422_sem_persistir(
    client: TestClient, repository: DespesaRepository, payload: dict[str, object]
) -> None:
    response = client.post("/despesas", json=payload)

    assert response.status_code == 422
    assert repository.count() == 0


@pytest.mark.parametrize(
    "payload",
    [{}, {"valor": None}, {"descricao": "x"}, {"campo_extra": "nao permitido"}],
)
def test_atualizacao_vazia_ou_invalida_retorna_422_e_preserva_registro(
    client: TestClient, database_path: Path, payload: dict[str, object]
) -> None:
    criada = criar_despesa(client)

    response = client.put(f"/despesas/{criada['id']}", json=payload)

    assert response.status_code == 422
    assert buscar_registro(database_path, criada["id"]) == (
        criada["id"],
        "Conta de energia",
        "125.50",
        "2026-05-10",
        "moradia",
        "debito",
    )


@pytest.mark.parametrize(
    ("params", "expected_status"),
    [
        ({"limit": 0}, 422),
        ({"limit": 101}, 422),
        ({"offset": -1}, 422),
        ({"forma_pagamento": "dinheiro"}, 422),
        ({"data_inicio": "2026-05-11", "data_fim": "2026-05-10"}, 422),
        ({"data_inicio": "nao-e-data"}, 422),
        ({"categoria": "x"}, 422),
    ],
)
def test_listagem_rejeita_filtros_invalidos(
    client: TestClient, params: dict[str, object], expected_status: int
) -> None:
    response = client.get("/despesas", params=params)

    assert response.status_code == expected_status


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/despesas/id-invalido", None),
        ("put", "/despesas/id-invalido", {"descricao": "Alterada"}),
        ("delete", "/despesas/id-invalido", None),
    ],
)
def test_id_com_formato_invalido_retorna_422(
    client: TestClient, method: str, path: str, payload: dict[str, str] | None
) -> None:
    response = client.request(method, path, json=payload)

    assert response.status_code == 422


def test_falha_de_banco_no_cadastro_retorna_erro_500_sem_expor_detalhes(
    repository: DespesaRepository,
) -> None:
    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_repository] = lambda: repository

    def falhar_create(payload: object) -> None:
        raise sqlite3.OperationalError("detalhe interno do banco")

    repository.create = falhar_create  # type: ignore[method-assign]
    try:
        with TestClient(app, raise_server_exceptions=False) as test_client:
            response = test_client.post(
                "/despesas",
                json={
                    "descricao": "Conta de energia",
                    "valor": "125.50",
                    "data_transacao": "2026-05-10",
                    "categoria": "moradia",
                    "forma_pagamento": "debito",
                },
            )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)

    assert response.status_code == 500
    assert "detalhe interno do banco" not in response.text
