"""Text slots a repair may edit. Code nodes are not slots."""
import copy
import re


def _node(workflow, name):
    for node in workflow.get("nodes") or []:
        if node.get("name") == name:
            return node
    return None


def conversation(workflow, name):
    node = _node(workflow, name)
    return node if node and node.get("node_type") == "conversation" else None


def get_text(workflow, slot):
    node = conversation(workflow, slot["node"])
    if node is None:
        return ""
    spec = slot["spec"]
    if spec == "task_message":
        return node.get("task_message") or ""
    if spec == "tool":
        for tool in node.get("tools") or []:
            function = (tool.get("function") or {})
            if function.get("name") == slot["key"]:
                return function.get("description") or ""
    if spec == "extract":
        for field in node.get("extraction_schema") or []:
            if field.get("name") == slot["key"]:
                return field.get("description") or ""
    if spec == "transition":
        for transition in node.get("transitions") or []:
            if transition.get("description") == slot["key"]:
                return transition.get("description") or ""
    return ""


def set_text(workflow, slot, text):
    """Replace one slot. Returns False when the slot is missing or the node is not a conversation."""
    node = conversation(workflow, slot["node"])
    if node is None:
        return False
    spec = slot["spec"]
    if spec == "task_message":
        node["task_message"] = text
        return True
    if spec == "tool":
        for tool in node.get("tools") or []:
            function = tool.get("function") or {}
            if function.get("name") == slot["key"]:
                function["description"] = text
                return True
    if spec == "extract":
        for field in node.get("extraction_schema") or []:
            if field.get("name") == slot["key"]:
                field["description"] = text
                return True
    if spec == "transition":
        for transition in node.get("transitions") or []:
            if transition.get("description") == slot["key"]:
                transition["description"] = text
                slot["key"] = text
                return True
    return False


def clone(workflow):
    return copy.deepcopy(workflow)


_TOKEN = re.compile(r"\{\{[^}]+\}\}")


def same_tokens(old, new):
    return set(_TOKEN.findall(old)) <= set(_TOKEN.findall(new))


def accept_edit(old, new):
    """A slot edit replaces the text. It keeps every {{token}}. It does not add a section."""
    if not isinstance(new, str) or not new.strip():
        return False
    if not isinstance(old, str) or not old.strip():
        return False
    if len(new) > len(old) + 400:
        return False
    if new.count("\n\n") > old.count("\n\n") + 1:
        return False
    if not same_tokens(old, new):
        return False
    return True
