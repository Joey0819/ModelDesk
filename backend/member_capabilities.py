"""Resolve saved roles by actual output capability, including legacy image roles."""
import re


def image_model(model):
    leaf = (model or '').split(':')[-1].split('/')[-1].lower()
    return bool(re.match(r'gpt-image(?:-|$)|grok.*image|gemini.*image', leaf))


def member_kind(member):
    kind = member.get('kind', 'model')
    return 'image' if kind == 'model' and image_model(member.get('model')) else kind
