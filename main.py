# -*- coding: utf-8 -*-
"""
Лабораторная работа: граф металлургических ресурсов.
Вариант 3 — граф ТЕРМИНОВ: узлы — термины, рёбра — совместная встречаемость.
Экспорт в .dot для онлайн-вьюера (без системного Graphviz).
"""

import re
import time
import logging
from urllib.parse import urlparse
from collections import Counter, defaultdict

import requests
from bs4 import BeautifulSoup
import rutermextract
from graphviz import Digraph

# ----------------------------- НАСТРОЙКИ ----------------------------------
TOP_TERMS_PER_PAGE = 40
TIMEOUT = 15
K = 3                        # минимальная совместная встречаемость для ребра
OUTPUT_NAME = "metallurgy_terms"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/122.0 Safari/537.36"),
    "Accept-Language": "ru,en;q=0.8",
}

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# --------------------------- СТОП-СЛОВА ------------------------------------
STOPWORDS = {
    "также", "который", "являться", "использоваться", "иметь",
    "мочь", "это", "весь", "свой", "такой", "например", "кроме",
    "более", "менее", "самый", "другой", "некоторый", "один",
    "год", "век", "часть", "случай", "образ", "вид", "место",
    "время", "работа", "процесс", "становиться", "называться",
    "дата", "обращение", "isbn", "issn", "doi", "гост", "ту", "ред",
    "источник", "источники", "ссылка", "ссылки", "примечание",
    "примечания", "литература", "см", "archived", "проверено",
    "мир", "начало", "категория", "шаблон", "статья", "сайт",
    "версия", "архив", "архивировано", "получено",
}

BANNED_SUBSTRINGS = (
    "дата обращения", "isbn", "issn", "doi", "гост",
    "источник", "источники", "ссылка", "примечание",
    "2 o", "ред.", "проверено", "архив",
    "волшебные ссылки", "википедия", "викиданные",
    "категория", "commons", "wikidata",
    "http", "www", "html",
)

# ---------------------- 1. СПИСОК НАЧАЛЬНЫХ URL ----------------------------
START_URLS = [
    # Википедия — металлургия
    "https://ru.wikipedia.org/wiki/Металлургия",
    "https://ru.wikipedia.org/wiki/Чёрная_металлургия",
    "https://ru.wikipedia.org/wiki/Цветная_металлургия",
    "https://ru.wikipedia.org/wiki/Доменная_печь",
    "https://ru.wikipedia.org/wiki/Сталь",
    "https://ru.wikipedia.org/wiki/Чугун",
    "https://ru.wikipedia.org/wiki/Алюминий",
    "https://ru.wikipedia.org/wiki/Медь",
    "https://ru.wikipedia.org/wiki/Никель",
    "https://ru.wikipedia.org/wiki/Титан",
    "https://ru.wikipedia.org/wiki/Прокат",
    "https://ru.wikipedia.org/wiki/Пирометаллургия",
    "https://ru.wikipedia.org/wiki/Гидрометаллургия",
    "https://ru.wikipedia.org/wiki/Электрометаллургия",
    "https://ru.wikipedia.org/wiki/Металловедение",
    "https://ru.wikipedia.org/wiki/Литейное_производство",
    "https://ru.wikipedia.org/wiki/Обогащение_полезных_ископаемых",
    "https://ru.wikipedia.org/wiki/Флотация",
    "https://ru.wikipedia.org/wiki/Окатыши",
    "https://ru.wikipedia.org/wiki/Агломерация_(металлургия)",
    "https://ru.wikipedia.org/wiki/Конвертерное_производство",
    "https://ru.wikipedia.org/wiki/Мартеновская_печь",
    "https://ru.wikipedia.org/wiki/Электросталеплавильное_производство",
    "https://ru.wikipedia.org/wiki/Непрерывное_литьё",
    "https://ru.wikipedia.org/wiki/Ферросплавы",
    "https://ru.wikipedia.org/wiki/Кокс",
    "https://ru.wikipedia.org/wiki/Шихта",
    # Внешние источники
    "https://metalinfo.ru/ru/news/",
    "https://metalinfo.ru/ru/articles/",
    "https://www.steelland.ru/news/",
    "https://www.steelland.ru/analytics/",
    "https://mc.ru/company/",
    "https://mc.ru/analytics/",
    "https://rusmet.ru/news/",
    "https://www.metalbulletin.ru/news/",
    "https://www.metalbulletin.ru/articles/",
]

extractor = rutermextract.TermExtractor()


# ------------------------ 2-4. ЗАГРУЗКА И ИЗВЛЕЧЕНИЕ ------------------------

def fetch_html(url: str) -> str | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        if "text/html" not in r.headers.get("Content-Type", ""):
            log.warning("Не HTML: %s", url)
            return None
        r.encoding = r.apparent_encoding or r.encoding
        return r.text
    except Exception as e:
        log.warning("Ошибка загрузки %s: %s", url, e)
        return None


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header",
                     "aside", "form", "noscript"]):
        tag.decompose()
    parts = []
    for tag in soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li"]):
        txt = tag.get_text(" ", strip=True)
        if txt and len(txt) > 20:
            parts.append(txt)
    return "\n".join(parts)


def is_junk(term: str) -> bool:
    t = term.lower().strip()
    if len(t) < 4 or len(t) > 60:
        return True
    if re.fullmatch(r"[\d\W_]+", t):
        return True
    if t in STOPWORDS:
        return True
    if any(b in t for b in BANNED_SUBSTRINGS):
        return True
    if re.fullmatch(r"[a-zа-я]{0,2}\s*\d+[a-zа-я]?", t):
        return True
    return False


def extract_terms(text: str) -> set[str]:
    if not text:
        return set()
    try:
        keywords = extractor(text, limit=TOP_TERMS_PER_PAGE, strings=True)
    except TypeError:
        keywords = extractor(text)
    except Exception as e:
        log.warning("Ошибка экстрактора: %s", e)
        return set()

    terms: set[str] = set()
    for kw in keywords:
        term = kw if isinstance(kw, str) else getattr(kw, "normalized", str(kw))
        term = term.lower().strip()
        if is_junk(term):
            continue
        terms.add(term)
    return terms


# --------------------------- ГЛАВНЫЙ ПАЙПЛАЙН ------------------------------

def main():
    # домен → множество терминов
    # URL → множество терминов (по КАЖДОЙ странице отдельно)
    site_terms: dict[str, set[str]] = {}

    log.info("Загружаем %d страниц...", len(START_URLS))
    for url in START_URLS:
        log.info("→ %s", url)
        html = fetch_html(url)
        if not html:
            continue
        terms = extract_terms(extract_text(html))
        if len(terms) < 5:
            log.warning("Слишком мало терминов (%d) для %s", len(terms), url)
            continue
        site_terms[url] = terms
        log.info("   терминов: %d", len(terms))
        time.sleep(0.5)

    log.info("Успешно обработано страниц: %d", len(site_terms))

    # ------------------- 5. СОВМЕСТНАЯ ВСТРЕЧАЕМОСТЬ ТЕРМИНОВ --------------
    cooc = Counter()
    for terms in site_terms.values():
        for a in terms:
            for b in terms:
                if a < b:
                    cooc[(a, b)] += 1

    term_edges = [(a, b, c) for (a, b), c in cooc.items() if c >= K]
    log.info("Найдено рёбер между терминами (K=%d): %d", K, len(term_edges))

    # ------------------- 6. ПОСТРОЕНИЕ ГРАФА ТЕРМИНОВ ---------------------
    dot = Digraph("MetallurgyTerms", format="pdf")
    dot.attr(rankdir="LR", overlap="false", splines="true")
    dot.attr("node", shape="ellipse", style="filled",
             fillcolor="lightblue", fontname="Arial", fontsize="10")
    dot.attr("edge", fontname="Arial", fontsize="8", color="gray40")

    nodes = {t for a, b, _ in term_edges for t in (a, b)}
    log.info("Узлов-терминов в графе: %d", len(nodes))

    for t in nodes:
        dot.node(t, label=t)

    for a, b, c in term_edges:
        dot.edge(a, b, label=str(c), penwidth=str(1 + c / 5))

    dot_path = dot.save(OUTPUT_NAME + ".dot")
    log.info("DOT-файл сохранён: %s", dot_path)
    print("\nОткройте этот .dot на одном из онлайн-вьюеров Graphviz:")
    print("  • https://dreampuf.github.io/GraphvizOnline/")
    print("  • https://magjac.com/graphviz-visual-editor/")
    print("  • https://edotor.net/")

    # ------------------- СВОДКА: ТОП-15 ТЕРМИНОВ ПО СТЕПЕНИ ---------------
    print("\n===== ТОП-15 ТЕРМИНОВ ПО ЧИСЛУ СВЯЗЕЙ =====")
    degree = Counter()
    for a, b, _ in term_edges:
        degree[a] += 1
        degree[b] += 1
    for term, d in degree.most_common(15):
        print(f"  {d:3d}  {term}")


if __name__ == "__main__":
    main()