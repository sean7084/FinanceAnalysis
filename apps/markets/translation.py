from modeltranslation.translator import register, TranslationOptions
from .models import Asset

@register(Asset)
class AssetTranslationOptions(TranslationOptions):
    fields = ('name',)
