from django import forms
from django.forms import inlineformset_factory
from .models import ValorTasaAnual, Liquidacion, LiquidacionPeriodo, PlanDePago


# ════════════════════════════════════════════════════════════════════════════
# VALOR DE TASA ANUAL
# ════════════════════════════════════════════════════════════════════════════

class ValorTasaAnualForm(forms.ModelForm):
    class Meta:
        model  = ValorTasaAnual
        fields = ["anio", "valor_m2", "recargo_iluminado_pct", "observaciones"]
        widgets = {
            "anio":                   forms.NumberInput(attrs={"class": "form-control"}),
            "valor_m2":               forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "recargo_iluminado_pct":  forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "observaciones":          forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }


# ════════════════════════════════════════════════════════════════════════════
# LIQUIDACIÓN
# ════════════════════════════════════════════════════════════════════════════

class LiquidacionForm(forms.ModelForm):
    class Meta:
        model  = Liquidacion
        fields = [
            "cartel",
            "superficie_m2",
            "es_iluminado",
            "aplica_descuento_ruta63",
            "km_ruta63",
            "sobre_ruta2",
            "observaciones",
        ]
        widgets = {
            "cartel":                  forms.Select(attrs={"class": "form-select"}),
            "superficie_m2":           forms.NumberInput(attrs={"class": "form-control", "step": "0.0001"}),
            "km_ruta63":               forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "observaciones":           forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "es_iluminado":            forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "aplica_descuento_ruta63": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "sobre_ruta2":             forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from carteles.models import Cartel
        self.fields["cartel"].queryset = Cartel.objects.filter(
            estado_procesamiento="ok",
            estado_registro="activo",
        ).select_related("parcela", "propietario_cartel")


# ════════════════════════════════════════════════════════════════════════════
# LIQUIDACIÓN PERÍODO
# ════════════════════════════════════════════════════════════════════════════

class LiquidacionPeriodoForm(forms.ModelForm):
    class Meta:
        model  = LiquidacionPeriodo
        fields = ["valor_tasa", "anio_fiscal", "tasa_mora_mensual"]
        widgets = {
            "valor_tasa":        forms.Select(attrs={"class": "form-select periodo-valor-tasa"}),
            "anio_fiscal":       forms.NumberInput(attrs={"class": "form-control"}),
            "tasa_mora_mensual": forms.NumberInput(attrs={"class": "form-control", "step": "0.0001"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["valor_tasa"].queryset = ValorTasaAnual.objects.all().order_by("-anio")
        self.fields["valor_tasa"].label_from_instance = lambda obj: f"{obj.anio} — ${obj.valor_m2}/m²"


# Formset inline de períodos dentro de la liquidación
LiquidacionPeriodoFormSet = inlineformset_factory(
    Liquidacion,
    LiquidacionPeriodo,
    form=LiquidacionPeriodoForm,
    extra=1,
    can_delete=True,
    min_num=1,
    validate_min=True,
)


# ════════════════════════════════════════════════════════════════════════════
# PLAN DE PAGO
# ════════════════════════════════════════════════════════════════════════════

class PlanDePagoForm(forms.ModelForm):
    class Meta:
        model  = PlanDePago
        fields = [
            "monto_anticipo",
            "cantidad_cuotas",
            "tasa_financiacion_mensual",
            "observaciones",
        ]
        widgets = {
            "monto_anticipo":            forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "cantidad_cuotas":           forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
            "tasa_financiacion_mensual": forms.NumberInput(attrs={"class": "form-control", "step": "0.0001"}),
            "observaciones":             forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }