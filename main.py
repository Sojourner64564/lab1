import re
import time
import logging
from urllib.parse import urlparse
from itertools import combinations

import requests
from bs4 import BeautifulSoup
import rutermextract
import graphviz
from graphviz import Digraph

# ----------------------------- НАСТРОЙКИ ----------------------------------
MIN_INTERSECTION = 8           # порог N для создания ребра
TOP_TERMS_PER_PAGE = 40        # сколько ключевых терминов брать с каждой страницы
TIMEOUT = 15                   # таймаут запроса
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/122.0 Safari/537.36"),
    "Accept-Language": "ru,en;q=0.8",
}
OUTPUT_NAME = "metallurgy_graph"   # имя выходного файла без расширения

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
STOPWORDS = {
    "также", "который", "являться", "использоваться", "иметь",
    "мочь", "это", "весь", "свой", "такой", "например", "кроме",
    "более", "менее", "самый", "другой", "некоторый", "один",
    "год", "век", "часть", "случай", "образ", "вид", "место",
    "время", "работа", "процесс", "становиться", "называться",
}

# ---------------------- 1. СПИСОК НАЧАЛЬНЫХ URL ----------------------------
START_URLS = [
    # Металлургические порталы и справочники
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
    "https://metalinfo.ru/",
    "https://www.steelland.ru/",
    "https://mc.ru/",
    "https://rusmet.ru/",
    "https://www.metalbulletin.ru/",
    "https://cyberleninka.ru/article/n/...",  # конкретная статья
    "https://www. металлург.рф/",
]

# ------------------------ 2-4. ЗАГРУЗКА И ИЗВЛЕЧЕНИЕ ------------------------

# Инициализируем экстрактор rutermextract один раз
extractor = rutermextract.TermExtractor()


def fetch_html(url: str) -> str | None:
    """Скачивает страницу. Возвращает HTML или None."""
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
    """Извлекает основной текст: p, h1-h6, li."""
    soup = BeautifulSoup(html, "lxml")
    # Удаляем мусор
    for tag in soup(["script", "style", "nav", "footer", "header",
                     "aside", "form", "noscript"]):
        tag.decompose()

    parts = []
    for tag in soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li"]):
        txt = tag.get_text(" ", strip=True)
        if txt and len(txt) > 20:     # отсеиваем короткие огрызки
            parts.append(txt)
    return "\n".join(parts)


def extract_terms(text: str) -> set[str]:
    """Извлекает ключевые термины в нормальной форме (rutermextract 0.3)."""
    if not text:
        return set()
    try:
        # strings=True  → список строк (нормализованных)
        # limit=N       → не больше N терминов
        keywords = extractor(text, limit=TOP_TERMS_PER_PAGE, strings=True)
    except TypeError:
        # страховка на случай другой версии API
        keywords = extractor(text)
    except Exception as e:
        log.warning("Ошибка экстрактора: %s", e)
        return set()

    terms: set[str] = set()
    for kw in keywords:
        # strings=True → kw это str; иначе — объект Term с полем .normalized
        term = kw if isinstance(kw, str) else getattr(kw, "normalized", str(kw))
        term = term.lower().strip()
        if len(term) < 3:
            continue
        if re.fullmatch(r"[\d\W_]+", term):
            continue
        if term in STOPWORDS:
            continue
        terms.add(term)
    return terms


def label_from_url(url: str) -> str:
    """Короткая подпись узла из URL."""
    path = urlparse(url).path
    name = path.rstrip("/").split("/")[-1]
    if not name:
        name = urlparse(url).netloc
    return name.replace("_", " ")[:40] or urlparse(url).netloc


# --------------------------- ГЛАВНЫЙ ПАЙПЛАЙН ------------------------------

def main():
    site_terms: dict[str, set[str]] = {}

    log.info("Загружаем %d страниц...", len(START_URLS))
    for url in START_URLS:
        log.info("→ %s", url)
        html = fetch_html(url)
        if not html:
            continue
        text = extract_text(html)
        terms = extract_terms(text)
        if len(terms) < 5:
            log.warning("Слишком мало терминов (%d) для %s", len(terms), url)
            continue
        site_terms[url] = terms
        log.info("   терминов: %d", len(terms))
        time.sleep(0.5)   # вежливость к серверам

    log.info("Успешно обработано: %d страниц", len(site_terms))

    # ------------------- 5. СРАВНЕНИЕ ТЕРМИНОВ ---------------------------
    edges = []
    urls = list(site_terms.keys())
    for a, b in combinations(urls, 2):
        common = site_terms[a] & site_terms[b]
        if len(common) >= MIN_INTERSECTION:
            edges.append((a, b, common))

    log.info("Найдено рёбер: %d", len(edges))

    # ------------------- 6. ПОСТРОЕНИЕ ГРАФА -----------------------------
    dot = Digraph("Metallurgy", format="pdf")
    dot.attr(rankdir="LR", overlap="false", splines="true")
    dot.attr("node", shape="ellipse", style="filled",
             fillcolor="lightyellow", fontname="Arial", fontsize="10")
    dot.attr("edge", fontname="Arial", fontsize="8", color="gray40")

    # Узлы
    for url in urls:
        dot.node(url, label=label_from_url(url),
                 tooltip=url,
                 fillcolor="lightblue" if edges and
                 any(url in (a, b) for a, b, _ in edges) else "lightyellow")

    # Рёбра — толщина пропорциональна числу общих терминов
    for a, b, common in edges:
        penwidth = str(1 + len(common) / 10)
        # первые несколько общих терминов — в подпись
        sample = ", ".join(sorted(common)[:3])
        dot.edge(a, b, label=f"{len(common)}: {sample}",
                 penwidth=penwidth, tooltip=", ".join(sorted(common)))

    #out_path = dot.render(OUTPUT_NAME, cleanup=True)
    #log.info("Граф сохранён: %s (и .pdf)", out_path)

    # Также сохраняем SVG
    #dot.format = "svg"
    #dot.render(OUTPUT_NAME, cleanup=True)
    #log.info("SVG-версия: %s.svg", OUTPUT_NAME)

    dot_path = dot.save(OUTPUT_NAME + ".dot")
    log.info("DOT-файл сохранён: %s", dot_path)
    print("\nОткройте этот .dot на одном из онлайн-вьюеров Graphviz:")
    print("  • https://dreampuf.github.io/GraphvizOnline/")
    print("  • https://magjac.com/graphviz-visual-editor/")
    print("  • https://edotor.net/")


    # Небольшая сводка
    print("\n===== ТОП-10 САМЫХ СВЯЗАННЫХ УЗЛОВ =====")
    degree = {u: 0 for u in urls}
    for a, b, _ in edges:
        degree[a] += 1
        degree[b] += 1
    for u, d in sorted(degree.items(), key=lambda x: -x[1])[:10]:
        print(f"  {d:3d}  {u}")


if __name__ == "__main__":
    main()