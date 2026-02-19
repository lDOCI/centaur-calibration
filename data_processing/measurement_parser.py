#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Модуль для парсинга данных измерений стола из конфигурационного файла Centaur.

Формат файла (Centaur / Klipper-стиль без #*#):
    [section_name]
    key : value

Карты меша хранятся в секциях с префиксом 'besh_profile_' или 'bed_mesh_profile_':
    [besh_profile_standard_default]
    version : 1
    points : v1, v2, v3, ...
    x_count : 11
    y_count : 11
    mesh_min : 20.0, 20.0
    mesh_max : 246.0, 246.0
    algo : bicubic

Карты всегда квадратные. Размер определяется:
  1. Из полей x_count / y_count (приоритет)
  2. Как isqrt(len(points)) если поля отсутствуют
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np


# ───────────────────────────────────────────────────────────
#  Структуры данных
# ───────────────────────────────────────────────────────────

@dataclass
class MeshData:
    """Структура данных для хранения одной карты измерений стола."""
    name: str              # Имя профиля (из заголовка секции)
    matrix: np.ndarray     # Матрица значений высот [y_count x x_count]
    x_count: int           # Количество точек по X
    y_count: int           # Количество точек по Y
    min_x: float           # Минимальная координата X
    max_x: float           # Максимальная координата X
    min_y: float           # Минимальная координата Y
    max_y: float           # Максимальная координата Y
    algo: str = ""         # Алгоритм интерполяции (bicubic / lagrange)

    @property
    def flat_points(self) -> List[float]:
        """Плоский список значений построчно."""
        return self.matrix.flatten().tolist()

    @property
    def min_value(self) -> float:
        return float(np.min(self.matrix))

    @property
    def max_value(self) -> float:
        return float(np.max(self.matrix))

    @property
    def range_value(self) -> float:
        return self.max_value - self.min_value

    @property
    def mean_value(self) -> float:
        return float(np.mean(self.matrix))

    def __repr__(self) -> str:
        return (
            f"MeshData(name={self.name!r}, size={self.x_count}x{self.y_count}, "
            f"range={self.range_value:.3f}mm, algo={self.algo!r})"
        )


# ───────────────────────────────────────────────────────────
#  Парсер
# ───────────────────────────────────────────────────────────

# Префиксы секций с картами меша.
# Centaur использует «besh» вместо «bed_mesh» — опечатка в прошивке.
_MESH_PREFIXES: Tuple[str, ...] = (
    "besh_profile_",
    "bed_mesh_profile_",
)


class FlashforgeMeshParser:
    """
    Парсер конфигурационного файла Centaur (Klipper-стиль, без #*#).

    Пример использования:
        parser = FlashforgeMeshParser()
        profiles = parser.parse_config_file(content)   # Dict[str, MeshData]

        # Список имён карт для отображения в UI:
        names = list(profiles.keys())

        # Получить конкретный профиль:
        mesh = profiles.get("standard_default")
    """

    # [section name]  — может содержать пробелы и символы
    _RE_SECTION = re.compile(r"^\[([^\]]+)\]\s*$")
    # key : value  — пробелы вокруг двоеточия опциональны; не захватываем комментарии
    _RE_PARAM = re.compile(r"^([^:#][^:]*?)\s*:\s*(.*?)\s*$")

    # ── публичный API ───────────────────────────────────────

    def parse_config_file(self, content: str) -> Dict[str, MeshData]:
        """
        Разбирает содержимое printer.cfg и возвращает все найденные карты меша.

        Args:
            content: Строка с полным содержимым файла.

        Returns:
            Словарь {profile_name: MeshData}.
            Пустой словарь, если карты не найдены или при ошибке парсинга.
        """
        sections = self._split_sections(content)
        profiles: Dict[str, MeshData] = {}

        for section_name, params in sections.items():
            profile_name = self._extract_mesh_profile_name(section_name)
            if profile_name is None:
                continue

            mesh = self._build_mesh_data(profile_name, params)
            if mesh is not None:
                profiles[profile_name] = mesh

        return profiles

    def validate_mesh_data(self, mesh_data: MeshData) -> bool:
        """
        Проверяет корректность данных измерений.

        Args:
            mesh_data: Структура с данными измерений.

        Returns:
            True если данные валидны.
        """
        # Проверяем размерность матрицы
        if mesh_data.matrix.shape != (mesh_data.y_count, mesh_data.x_count):
            return False

        # Проверяем на NaN / Inf
        if np.any(np.isnan(mesh_data.matrix)) or np.any(np.isinf(mesh_data.matrix)):
            return False

        # Типичные отклонения стола не превышают ±10 мм
        if np.any(np.abs(mesh_data.matrix) > 10):
            return False

        return True

    # ── внутренние методы ───────────────────────────────────

    def _split_sections(self, content: str) -> Dict[str, Dict[str, str]]:
        """
        Разбивает файл на секции.

        Returns:
            {section_name_lowercase: {key: value, ...}}
        """
        sections: Dict[str, Dict[str, str]] = {}
        current_section: Optional[str] = None
        current_params: Dict[str, str] = {}

        for raw_line in content.splitlines():
            line = raw_line.strip()

            # Пропускаем пустые строки и комментарии
            if not line or line.startswith("#"):
                continue

            # Удаляем инлайн-комментарии (после #)
            if "#" in line:
                line = line[:line.index("#")].strip()
            if not line:
                continue

            # Новая секция?
            m = self._RE_SECTION.match(line)
            if m:
                # Сохраняем предыдущую секцию
                if current_section is not None:
                    sections[current_section] = current_params

                current_section = m.group(1).strip().lower()
                current_params = {}
                continue

            # Параметр key : value
            m = self._RE_PARAM.match(line)
            if m and current_section is not None:
                key = m.group(1).strip().lower()
                value = m.group(2).strip()
                # points могут теоретически продолжаться на следующей строке
                if key == "points" and key in current_params:
                    current_params[key] += ", " + value
                else:
                    current_params[key] = value

        # Последняя секция
        if current_section is not None:
            sections[current_section] = current_params

        return sections

    @staticmethod
    def _extract_mesh_profile_name(section_name: str) -> Optional[str]:
        """
        Если секция является картой меша — возвращает имя профиля, иначе None.

        Примеры:
            "besh_profile_standard_default"  ->  "standard_default"
            "besh_profile_standard_1"        ->  "standard_1"
            "bed_mesh_profile_default"       ->  "default"
            "printer"                        ->  None
        """
        for prefix in _MESH_PREFIXES:
            if section_name.startswith(prefix):
                name = section_name[len(prefix):]
                return name if name else "default"
        return None

    def _build_mesh_data(
        self, profile_name: str, params: Dict[str, str]
    ) -> Optional[MeshData]:
        """
        Строит MeshData из словаря параметров секции.

        Returns:
            MeshData или None если данные некорректны.
        """
        try:
            # ── points ──────────────────────────────────────
            raw_points = params.get("points", "")
            if not raw_points:
                raise ValueError(f"Отсутствует поле 'points'")

            flat: List[float] = [
                float(v.strip())
                for v in raw_points.split(",")
                if v.strip()
            ]
            if not flat:
                raise ValueError("Пустой список points")

            # ── размер сетки ─────────────────────────────────
            x_count, y_count = self._resolve_grid_size(profile_name, params, flat)

            expected = x_count * y_count
            if expected != len(flat):
                raise ValueError(
                    f"Несоответствие размера: {x_count}x{y_count}={expected} "
                    f"не совпадает с количеством points={len(flat)}"
                )

            # ── формируем матрицу [y_count x x_count] ────────
            matrix = np.array(flat, dtype=np.float64).reshape(y_count, x_count)

            # ── координаты сетки ─────────────────────────────
            min_x, min_y, max_x, max_y = self._resolve_bounds(params)

            # ── алгоритм ─────────────────────────────────────
            algo = params.get("algo", params.get("algorithm", ""))

            return MeshData(
                name=profile_name,
                matrix=matrix,
                x_count=x_count,
                y_count=y_count,
                min_x=min_x,
                max_x=max_x,
                min_y=min_y,
                max_y=max_y,
                algo=algo,
            )

        except Exception as e:
            print(f"[FlashforgeMeshParser] Ошибка парсинга профиля '{profile_name}': {e}")
            return None

    @staticmethod
    def _resolve_grid_size(
        profile_name: str,
        params: Dict[str, str],
        flat: List[float],
    ) -> Tuple[int, int]:
        """
        Определяет размер сетки.

        Приоритет:
          1. x_count / y_count из параметров (могут называться mesh_x_pps и т.д. — нет,
             берём именно x_count / y_count)
          2. isqrt(len(flat)) — карты всегда квадратные
        """
        x_str = params.get("x_count", "")
        y_str = params.get("y_count", "")

        if x_str and y_str:
            return int(float(x_str)), int(float(y_str))

        # Автоопределение для квадратных карт
        n = len(flat)
        side = math.isqrt(n)
        if side * side != n:
            raise ValueError(
                f"[{profile_name}] Нет x_count/y_count и {n} точек "
                f"не является точным квадратом (√{n} ≈ {math.sqrt(n):.3f})"
            )
        return side, side

    @staticmethod
    def _resolve_bounds(params: Dict[str, str]) -> Tuple[float, float, float, float]:
        """
        Извлекает граничные координаты сетки.

        Поддерживает два формата:
          - mesh_min : 20.0, 20.0  и  mesh_max : 246.0, 246.0
          - min_x : 20.0, max_x : 246.0, min_y : 20.0, max_y : 246.0
        """
        mesh_min = params.get("mesh_min", "")
        mesh_max = params.get("mesh_max", "")

        if mesh_min and mesh_max:
            mn = [float(v.strip()) for v in mesh_min.split(",")]
            mx = [float(v.strip()) for v in mesh_max.split(",")]
            # mesh_min/max — порядок X, Y
            return mn[0], mn[1], mx[0], mx[1]

        # Отдельные поля
        try:
            min_x = float(params.get("min_x", params.get("x_min", "0")))
            max_x = float(params.get("max_x", params.get("x_max", "0")))
            min_y = float(params.get("min_y", params.get("y_min", "0")))
            max_y = float(params.get("max_y", params.get("y_max", "0")))
            return min_x, min_y, max_x, max_y
        except ValueError:
            return 0.0, 0.0, 0.0, 0.0


# ───────────────────────────────────────────────────────────
#  Быстрый тест при запуске напрямую
# ───────────────────────────────────────────────────────────

# ───────────────────────────────────────────────────────────
#  Обратная совместимость — старое имя класса
# ───────────────────────────────────────────────────────────
KlipperMeshParser = FlashforgeMeshParser


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python cfg_parser.py printer.cfg")
        sys.exit(1)

    cfg_path = sys.argv[1]
    with open(cfg_path, encoding="utf-8", errors="replace") as f:
        cfg_content = f.read()

    p = FlashforgeMeshParser()
    found = p.parse_config_file(cfg_content)

    if not found:
        print("Карты меша не найдены.")
        sys.exit(0)

    print(f"Найдено профилей: {len(found)}\n")
    for pname, mesh in found.items():
        ok = p.validate_mesh_data(mesh)
        print(f"  [{pname}]")
        print(f"    Размер  : {mesh.x_count} x {mesh.y_count}")
        print(f"    Алго    : {mesh.algo or '—'}")
        print(f"    Bounds  : X[{mesh.min_x}..{mesh.max_x}]  Y[{mesh.min_y}..{mesh.max_y}]")
        print(f"    Min     : {mesh.min_value:.4f} мм")
        print(f"    Max     : {mesh.max_value:.4f} мм")
        print(f"    Размах  : {mesh.range_value:.4f} мм")
        print(f"    Среднее : {mesh.mean_value:.4f} мм")
        print(f"    Валиден : {'YES' if ok else 'NO'}")
        print()