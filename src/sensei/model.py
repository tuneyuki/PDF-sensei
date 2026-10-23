"""Canonical 層（DocTree）のスキーマ。

EPUB / PDF のどちらから抽出しても同じ形に揃え、後段（チャンク化・要約・検索）は
入力フォーマットを意識しないで済むようにする。設計は docs/design/01-data-model-and-ingestion.md。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"

BlockType = Literal[
    "heading",
    "paragraph",
    "list",
    "table",
    "figure",
    "code",
    "quote",
    "note",  # 囲み記事・注意書き（admonition）
    "footnote",
    "equation",
]

SectionRole = Literal["front", "body", "back", "toc", "index"]


class PdfLocator(BaseModel):
    page: int  # 1 始まりの物理ページ番号
    bbox: tuple[float, float, float, float]


class EpubLocator(BaseModel):
    href: str  # spine 上の XHTML（パッケージ相対パス）
    element_id: str | None = None  # 最寄りの祖先要素の id（リンク用）
    cfi: str | None = None


class Locator(BaseModel):
    pdf: list[PdfLocator] | None = None  # ページをまたぐ段落は複数
    epub: EpubLocator | None = None


class Block(BaseModel):
    block_id: str = ""
    section_id: str = ""
    order: int = 0
    type: BlockType
    text: str
    level: int | None = None  # heading のみ
    items: list[str] | None = None  # list のみ
    language: str | None = None  # code のみ（分かれば）
    caption: str | None = None  # figure / table / code の見出し
    asset_id: str | None = None  # figure の画像
    locator: Locator = Field(default_factory=Locator)
    page_label: str | None = None  # 書籍に印字されたページ番号
    annotations: dict | None = None  # ルビなど本文から分離した情報
    flags: list[str] = Field(default_factory=list)


class Section(BaseModel):
    section_id: str
    parent_id: str | None = None
    level: int
    title: str
    heading_path: list[str]
    order: int
    role: SectionRole = "body"
    page_start: int | None = None
    page_end: int | None = None
    page_label_start: str | None = None
    page_label_end: str | None = None
    href: str | None = None
    block_count: int = 0
    char_count: int = 0


class Asset(BaseModel):
    asset_id: str
    path: str  # doc.json からの相対パス
    media_type: str
    caption: str | None = None
    alt: str | None = None


class TocEntry(BaseModel):
    level: int
    title: str
    page: int | None = None  # PDF のしおり
    href: str | None = None  # EPUB の nav
    section_id: str | None = None  # 突き合わせ結果


class Extraction(BaseModel):
    extractor: str
    extractor_version: str
    ocr_used: bool = False
    params: dict = Field(default_factory=dict)


class BookMeta(BaseModel):
    book_id: str
    version: int = 1
    source_sha256: str
    source_format: Literal["epub", "pdf"]
    source_filename: str
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    publisher: str | None = None
    isbn: str | None = None
    language: str | None = None
    published_at: str | None = None
    page_count: int | None = None
    extraction: Extraction


class DocTree(BaseModel):
    schema_version: str = SCHEMA_VERSION
    book: BookMeta
    toc: list[TocEntry] = Field(default_factory=list)
    sections: list[Section] = Field(default_factory=list)
    blocks: list[Block] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)


class RawDoc(BaseModel):
    """抽出器の出力。見出しを含むブロックの平坦な列。section 付与は structure 段で行う。"""

    meta: BookMeta
    toc: list[TocEntry] = Field(default_factory=list)
    blocks: list[Block] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
