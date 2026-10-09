"""Write an imported preference item back the way Windows wrote it (WI-080).

The typed GPP model holds what Studio edits. It does not hold everything an
item carries: a printer's ``default``, an environment variable's ``partial``, a
task's ``logonType``, a drive's ``thisDrive``/``allDrives`` and many more
``Properties`` attributes have no typed field, the writer adds default-valued
attributes the source never had (``removePolicy="0"``), normalises values
(``window=""`` becomes ``"Normal"``) and orders attributes its own way. Before
WI-080 a stored GPO was always written from the model alone, so all of that
was lost on every export.

Import now keeps each item's element as Windows wrote it (``native_xml`` on the
item). On write, :func:`merge_native` reconciles three renderings of the item:

* ``original``: the element as imported;
* ``imported``: what Studio's writer produces for the item as it was parsed
  from ``original`` (computed afresh on every write, never stored);
* ``current``: what Studio's writer produces for the item as it is now.

Where ``current`` equals ``imported``, nothing Studio models has changed, and
the original is written. Where they differ, the TYPED VALUE WINS, attribute by
attribute, and everything the edit did not touch keeps its original value and
place:

* an attribute the writer renders the same way it rendered at import keeps its
  original text (``window=""`` stays ``""``);
* an attribute whose rendering changed takes the new value, in its original
  position; one the writer no longer renders is gone;
* an attribute the writer never rendered stays as imported only if the parser
  does not read it (``is_modeled``): an unmodelled attribute is content the
  edit cannot have changed, while a modelled one the writer no longer renders
  (a legacy placement, say) would be stale;
* an attribute the writer renders that the source never had is left out while
  its rendering is unchanged (``removePolicy="0"``), and written once an edit
  changes it;
* children whose rendering is unchanged are written as imported; the
  ``Properties``, ``Filters`` and ``Members`` containers are merged the same
  way, recursively; any other child that changed is written from the model.

The rules can miss a case the parser handles across elements (an option an
older Studio wrote on ``Properties``, which the writer renders on the item). So
``gpp.py`` checks every merge: it parses the merged element and writes it again,
and unless that gives back the model's own rendering, it writes the model's
rendering instead. A retained attribute can cost an edit fidelity, never its
meaning.

The module is pure ElementTree: it knows nothing about families. ``gpp.py``
supplies the renderings and the ``is_modeled`` test.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from copy import deepcopy

#: Children merged attribute by attribute when they changed. Each occurs at
#: most once per item. Every other child is written either as imported (when
#: its rendering is unchanged) or from the model, never merged: a predicate or
#: a member whose typed value changed must not keep an attribute that described
#: the old value (a group filter's ``name`` beside a new ``sid``).
MERGED_CONTAINERS: frozenset[str] = frozenset({"Properties", "Filters", "Members"})

#: Never written from a retained copy, whatever the merge would decide. Import
#: refuses a cpassword, and so does every export; this keeps a retained copy
#: from ever being the way one gets back in.
NEVER_RETAINED: frozenset[str] = frozenset({"cpassword"})

#: ``is_modeled(path, attribute)``: does the parser read ``attribute`` of the
#: element at ``path`` (child indices from the item element, in ``original``)?
ModeledTest = Callable[[tuple[int, ...], str], bool]


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def credential_local_name(name: str) -> str:
    """*name*'s local part, for the credential ban: after any ``{namespace}``
    AND any literal ``prefix:``, case-folded (second re-check: a stored
    ``x:cpassword`` key passed a check of ``{namespace}`` names only)."""
    return _local_name(name).rsplit(":", 1)[-1].casefold()


#: A retained attribute name: an XML NCName in ASCII, so no ``prefix:``, no
#: ``{namespace}`` and not ``xmlns``. Every attribute name in the native
#: captures matches; a literal ``xmlns:x`` or ``x:extra`` key (which no parse
#: produces, but an API payload or a stored dict can carry) would be written
#: verbatim as a foreign namespace (second re-check).
_PLAIN_ATTRIBUTE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9._-]*")


def plain_attribute_name(name: str) -> bool:
    return bool(_PLAIN_ATTRIBUTE_NAME.fullmatch(name)) and name.casefold() != "xmlns"


def native_element_xml(elem: ET.Element) -> str:
    """The element as stored on the item: verbatim, without its trailing text.

    The tail belongs to the parent's formatting (GPMC indents root children
    with a newline and a tab), not to the item. An element that uses any XML
    namespace is not retained (``""``): no captured GPP item does (see
    `namespaced_name`), and a stored one is refused on load.
    """
    if namespaced_name(elem) is not None:
        return ""
    copy = deepcopy(elem)
    copy.tail = None
    return ET.tostring(copy, encoding="unicode")


def namespaced_name(elem: ET.Element) -> str | None:
    """The first namespace-qualified element or attribute name in *elem*, or ``None``.

    Every GPP item in the native captures, its payload included, uses
    unqualified names only. The parser matches LOCAL names, so a foreign
    ``{urn:x}SharedPrinter`` parses like a real one; written back verbatim it
    would carry a namespace no Windows file was seen to use (review P2). So a
    retained element may use no namespace at all, the ``xml:`` one included.
    """
    for node in elem.iter():
        if not isinstance(node.tag, str) or node.tag.startswith("{"):
            return str(node.tag)
        for name in node.attrib:
            if name.startswith("{"):
                return name
    return None


def has_never_retained_name(elem: ET.Element) -> bool:
    """Whether *elem* holds a ``cpassword``, as an attribute OR an element.

    Any depth, any case, any namespace (review: an element ``<cpassword>``
    passed a check of attribute names only, was stored, and was exported).
    """
    return any(
        credential_local_name(name) in NEVER_RETAINED
        for node in elem.iter()
        for name in (str(node.tag), *node.attrib)
    )


def _text(value: str | None) -> str:
    return value if value is not None and value.strip() else ""


def same_rendering(left: ET.Element, right: ET.Element) -> bool:
    """Two writer renderings of an element are the same document.

    Attribute order counts (the writer is deterministic, so a different order
    is a different rendering); whitespace-only text does not.
    """
    if (
        left.tag != right.tag
        or list(left.attrib.items()) != list(right.attrib.items())
        or _text(left.text) != _text(right.text)
        or len(left) != len(right)
    ):
        return False
    return all(
        _text(a.tail) == _text(b.tail) and same_rendering(a, b)
        for a, b in zip(left, right, strict=True)
    )


def _copy(elem: ET.Element) -> ET.Element:
    copy = deepcopy(elem)
    copy.tail = None
    return copy


def _merge_attributes(
    original: ET.Element,
    imported: ET.Element,
    current: ET.Element,
    is_modeled: Callable[[str], bool],
) -> list[tuple[str, str]]:
    then = dict(imported.attrib)
    now = dict(current.attrib)
    merged: list[tuple[str, str]] = []
    for name, value in original.attrib.items():
        if _local_name(name).casefold() in NEVER_RETAINED:
            continue
        if name in now:
            merged.append((name, value if then.get(name) == now[name] else now[name]))
        elif name in then:
            continue  # the writer rendered it at import and no longer does: edited away
        elif not is_modeled(name):
            merged.append((name, value))  # never typed: the edit cannot have touched it
    for name, value in current.attrib.items():
        if name in original.attrib:
            continue  # placed (or deliberately dropped) above
        if then.get(name) == value:
            continue  # a writer default the source never had, still unchanged
        merged.append((name, value))
    return merged


def merge_native(
    original: ET.Element,
    imported: ET.Element,
    current: ET.Element,
    is_modeled: ModeledTest,
    path: tuple[int, ...] = (),
) -> ET.Element:
    """The element to write for an item that was imported (see the module doc)."""
    if same_rendering(imported, current):
        return _copy(original)
    out = ET.Element(current.tag)
    if list(imported.attrib.items()) == list(current.attrib.items()):
        attributes = [
            (name, value)
            for name, value in original.attrib.items()
            if _local_name(name).casefold() not in NEVER_RETAINED
        ]
    else:
        attributes = _merge_attributes(
            original, imported, current, lambda name: is_modeled(path, name)
        )
    for name, value in attributes:
        out.set(name, value)
    out.text = original.text if _text(imported.text) == _text(current.text) else current.text
    out.extend(_merge_children(original, imported, current, is_modeled, path))
    return out


def _merge_children(
    original: ET.Element,
    imported: ET.Element,
    current: ET.Element,
    is_modeled: ModeledTest,
    path: tuple[int, ...],
) -> list[ET.Element]:
    then_children = list(imported)
    now_children = list(current)
    original_children = list(original)
    # The imported rendering stands for the original only child for child: if
    # the writer reshaped the item (a legacy layout, a reordered filter), the
    # model is written as it is.
    if [c.tag for c in original_children] != [c.tag for c in then_children]:
        return [_copy(child) for child in now_children]
    used: set[int] = set()
    written: list[ET.Element] = []
    for child in now_children:
        match = next(
            (
                index
                for index, candidate in enumerate(then_children)
                if index not in used and same_rendering(candidate, child)
            ),
            None,
        )
        if match is not None:
            used.add(match)
            written.append(_copy(original_children[match]))
            continue
        same_tag = [i for i, c in enumerate(then_children) if c.tag == child.tag]
        if (
            _local_name(child.tag) in MERGED_CONTAINERS
            and len(same_tag) == 1
            and same_tag[0] not in used
            and sum(1 for c in now_children if c.tag == child.tag) == 1
        ):
            index = same_tag[0]
            used.add(index)
            written.append(
                merge_native(
                    original_children[index],
                    then_children[index],
                    child,
                    is_modeled,
                    (*path, index),
                )
            )
            continue
        written.append(_copy(child))
    return written
