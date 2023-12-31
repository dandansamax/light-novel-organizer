import argparse
import logging
import re
import shutil
import tempfile
import zipfile
from functools import partial
from itertools import chain
from pathlib import Path
from typing import Optional

from lxml import etree
from unrar import rarfile
from py7zr import SevenZipFile

from bangumi_api import *


class Author:
    pass


class Book:
    def __init__(self, path: Path) -> None:
        assert path.suffix == ".epub"

        self.path = path
        meta_info = self.get_meta_info()
        self.title = meta_info["title"]
        self.title = clean_name(self.title)
        self.author = meta_info["creator"]
        self.series_name = None
        self.series_name = self.get_series_name()

        self.bangumi_id = None
        self.bangumi_name = None
        self.bangumi_authors = (None,)
        self.bangumi_illustrators = (None,)
        self.bangumi_producers = (None,)

    def get_meta_info(self) -> dict:
        """This function is taken from https://stackoverflow.com/a/3114929/17849851"""
        if hasattr(self, "meta_info") and self.meta_info is not None:
            return self.meta_info

        def xpath(element, path):
            return element.xpath(
                path,
                namespaces={
                    "n": "urn:oasis:names:tc:opendocument:xmlns:container",
                    "pkg": "http://www.idpf.org/2007/opf",
                    "dc": "http://purl.org/dc/elements/1.1/",
                },
            )[0]

        # prepare to read from the .epub file
        zip_content = zipfile.ZipFile(self.path)

        # find the contents metafile
        cfname = xpath(
            etree.fromstring(zip_content.read("META-INF/container.xml")),
            "n:rootfiles/n:rootfile/@full-path",
        )

        # grab the metadata block from the contents metafile
        metadata = xpath(
            etree.fromstring(zip_content.read(cfname)), "/pkg:package/pkg:metadata"
        )

        # repackage the data
        res = {}
        for s in ["title", "creator"]:
            try:
                res[s] = xpath(metadata, f"dc:{s}/text()")
            except Exception:
                res[s] = "unkown"
        return res
    
    def get_series_name(self):
        if self.series_name is not None:
            return self.series_name
        order_pattern = "第?([一二三四五六七八九十]|\d){1,3}卷?话?"
        title_pattern = f"^(\S+?)\s?({order_pattern}|\s({order_pattern}).*)$"
        m = re.match(title_pattern, self.title)
        if m is not None:
            return m.group(1)

        title_pattern = f"^(\S+?)\s.*$"
        m = re.match(title_pattern, self.title)
        if m is not None:
            return m.group(1)

        return None

    def get_bangumi_info(self):
        keyword = self.series_name or self.title
        logging.debug(f'Searching keyword "{keyword}".')
        result = search_novel(keyword)
        self.bangumi_id = result["id"]
        self.bangumi_name = result["name"]
        persons = get_person_by_id(self.bangumi_id)
        if not persons["authors"]:
            check = check_id(self.bangumi_id)
            if check is not None:
                self.bangumi_id = check["id"]
                self.bangumi_name = check["name"]
                persons = get_person_by_id(self.bangumi_id)
        self.bangumi_authors = persons["authors"]
        self.bangumi_illustrators = persons["illustrators"]
        self.bangumi_producers = persons["producers"]
        if not self.bangumi_authors:
            logging.warning(f'Cannot find authors of "{self.bangumi_name}"')

    def construct_output_path(self, output_root: Path) -> Path:
        if not self.bangumi_id:
            if self.series_name is not None:
                return (
                    output_root / self.author / self.series_name / f"{self.title}.epub"
                )
            else:
                return output_root / self.author / f"{self.title}.epub"

        if self.bangumi_authors:
            author_names = [f"{a[1]}[{a[0]}]" for a in self.bangumi_authors]
            author_names.sort()
            author_dir_name = "_".join(author_names)
        else:
            author_dir_name = self.author
        return (
            output_root
            / author_dir_name
            / f"{self.bangumi_name}[{self.bangumi_id}]"
            / f"{self.title}.epub"
        )


def clean_name(raw_name: str):
    return raw_name.replace('"', "")


PASSWDS = [None, b"tsdm", b"sbyr", b"light931"]


def get_compressed(path: Path, tmp_path: Path, action: Optional[callable] = None):
    logging.debug(f"handling {path}")
    extract_path = tmp_path / path.stem
    if path.suffix == ".zip":
        with zipfile.ZipFile(path, "r") as comp_file:
            for passwd in PASSWDS:
                try:
                    comp_file.extractall(str(extract_path), pwd=passwd)
                    break
                except RuntimeError:
                    pass
                except Exception:
                    logging.exception(f"At location {path}.")
    elif path.suffix == ".rar":
        with rarfile.RarFile(str(path), "r") as comp_file:
            for passwd in PASSWDS:
                try:
                    comp_file.extractall(str(extract_path), pwd=passwd)
                    break
                except RuntimeError:
                    pass
                except Exception:
                    logging.exception(f"At location {path}.")
    elif path.suffix == ".7z":
        for passwd in PASSWDS:
            try:
                with SevenZipFile(path, "r", password=passwd) as comp_file:
                    comp_file.extractall(str(extract_path))
                    break
            except Exception:
                logging.exception(f"At location {path}.")

    else:
        raise RuntimeError(f"{path} has an unknown suffix")

    logging.debug(f"extract dir: {extract_path}")
    if extract_path.is_dir() and any(extract_path.iterdir()):
        return get_books(extract_path, tmp_path, action)
    else:
        raise RuntimeError(f"{path} has an unknown password.")


def get_books(path, tmp_path, action: Optional[callable] = None) -> list[Book]:
    logging.info(f"get_books[{path}]")
    result = []
    for epub in path.rglob("*.epub"):
        result.append(epub)
        if action is not None:
            try:
                action(epub)
            except Exception:
                logging.exception(f"At location {path}.")
    for comp_file in chain(
        path.rglob("*.zip"), path.rglob("*.rar"), path.rglob("*.7z")
    ):
        try:
            result.extend(get_compressed(comp_file, tmp_path, action))
        except Exception:
            logging.exception(f"At location {path}.")

    return result


def organize_novel(path: Path, output_path: Path):
    book = Book(path)
    logging.info(f'Organize book: "{book.title}"')
    try:
        book.get_bangumi_info()
    except RuntimeError as e:
        fpath = book.construct_output_path(output_path)
        fpath.parent.mkdir(exist_ok=True, parents=True)
        shutil.copy(path, fpath)
        logging.warning(f"{e} At location {fpath}.")
        return

    fpath = book.construct_output_path(output_path)
    fpath.parent.mkdir(exist_ok=True, parents=True)
    shutil.copy(path, fpath)


def transfer(source_path, output_path, tmp_path=None):
    source_path = Path(source_path)
    output_path = Path(output_path)
    tmp_path = tmp_path or tempfile.TemporaryDirectory().name
    tmp_path = Path(tmp_path)
    tmp_path.mkdir(exist_ok=True)
    action = partial(organize_novel, output_path=output_path)
    get_books(source_path, tmp_path, action)


def log_config(args):
    LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
    info_handle = logging.StreamHandler()
    if args.verbose:
        info_handle.setLevel(logging.DEBUG)
    else:
        info_handle.setLevel(logging.INFO)

    warning_handle = logging.FileHandler(
        Path(output_path) / "warning.log", encoding="utf-8"
    )
    warning_handle.setLevel(logging.WARNING)

    error_handle = logging.FileHandler(
        Path(output_path) / "error.log", encoding="utf-8"
    )
    error_handle.setLevel(logging.ERROR)

    logging.basicConfig(
        handlers=[info_handle, warning_handle, error_handle],
        level=logging.DEBUG,
        format=LOG_FORMAT,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Easily manage your light novel repo")
    parser.add_argument(
        "input", nargs="+", help="the source directories storing lightnovels"
    )
    parser.add_argument("-o", "--output", help="the output directory")
    parser.add_argument("-t", "--temp", help="the temp directory")
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose output")
    args = parser.parse_args()

    source_paths = args.input if args.input is not None else ["."]
    output_path = args.output if args.output is not None else "output"
    Path(output_path).mkdir(exist_ok=True, parents=True)

    log_config(args)

    for source_path in source_paths:
        transfer(source_path, output_path, args.temp)
