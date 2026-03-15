from datetime import date

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404

from django.http import HttpResponse
from .models import ArchivoImportacion, Liquidacion
from .services import guardar_archivo, procesar_archivo, eliminar_archivo, revertir_procesamiento, consulta_por_deudor, consulta_por_parcela, totales_generales, generar_liquidacion, descargar_pdf_liquidacion, totales_liquidaciones



@login_required(login_url='login')
def home_view(request):
    """
    Dashboard principal:
    - Contador de archivos subidos sin procesar
    - Contador de archivos procesados
    - Lista de todos los archivos con su estado
    """
    archivos = ArchivoImportacion.objects.all()
    sin_procesar = archivos.filter(procesado=False).count()
    procesados   = archivos.filter(procesado=True).count()
    revertidos   = archivos.filter(revertido=True).count()

    return render(request, 'principal/home.html', {
        "archivos":     archivos,
        "sin_procesar": sin_procesar,
        "procesados":   procesados,
        "revertidos":   revertidos,
    })


@login_required(login_url='login')
def subir_archivo_view(request):
    """
    Recibe el archivo Excel y lo guarda en la BD sin procesarlo.
    POST → llama a guardar_archivo() y redirige al dashboard.
    """
    if request.method == "POST":
        archivo = request.FILES.get("archivo")
        periodo_str = request.POST.get("periodo")  # formato "YYYY-MM"

        # -- Validaciones del formulario --
        if not archivo:
            messages.error(request, "Seleccioná un archivo Excel.")
            return redirect("home")

        if not periodo_str:
            messages.error(request, "Seleccioná el período.")
            return redirect("home")

        ext = archivo.name.split(".")[-1].lower()
        if ext not in ["xlsx", "xls"]:
            messages.error(request, "El archivo debe ser un Excel (.xlsx o .xls).")
            return redirect("home")

        try:
            anio, mes = periodo_str.split("-")
            periodo = date(int(anio), int(mes), 1)
        except (ValueError, AttributeError):
            messages.error(request, "El período tiene un formato inválido.")
            return redirect("home")

        # -- Guardar --
        try:
            importacion = guardar_archivo(
                archivo_binario=archivo.read(),
                nombre_archivo=archivo.name,
                periodo=periodo,
                usuario=request.user,
            )
            messages.success(
                request,
                f"Archivo '{importacion.nombre_archivo}' subido correctamente. "
                f"Podés procesarlo cuando quieras."
            )
        except ValueError as e:
            messages.error(request, str(e))
        except Exception as e:
            messages.error(request, f"Error inesperado al subir el archivo: {str(e)}")

    return redirect("home")


@login_required(login_url='login')
def procesar_archivo_view(request, archivo_id):
    """
    Procesa un archivo ya subido e incorpora los datos a la BD.
    Solo acepta POST para evitar procesamientos accidentales por GET.
    """
    if request.method != "POST":
        return redirect("home")

    try:
        resumen = procesar_archivo(archivo_id)
        return render(request, 'principal/resultado.html', {
            "resumen": resumen,
            "archivo": get_object_or_404(ArchivoImportacion, pk=archivo_id),
        })
    except ValueError as e:
        messages.error(request, str(e))
        return redirect("home")
    except Exception as e:
        messages.error(request, f"Error inesperado al procesar: {str(e)}")
        return redirect("home")


@login_required(login_url='login')
def consultas_view(request):
    """
    Página de consultas: búsqueda por deudor, parcela y totales generales.
    """
    ctx = {
        "totales":       totales_generales(),
        "liquidaciones": totales_liquidaciones(),
    }

    if "deudor" in request.GET and request.GET["deudor"].strip():
        ctx["resultado_deudor"] = consulta_por_deudor(request.GET["deudor"].strip())
        ctx["busqueda_deudor"]  = request.GET["deudor"].strip()

    if "parcela" in request.GET and request.GET["parcela"].strip():
        ctx["resultado_parcela"] = consulta_por_parcela(request.GET["parcela"].strip())
        ctx["busqueda_parcela"]  = request.GET["parcela"].strip()

    return render(request, 'principal/consultas.html', ctx)

@login_required(login_url='login')
def eliminar_archivo_view(request, archivo_id):
    """
    Elimina un archivo subido que todavía no fue procesado.
    Solo acepta POST.
    """
    if request.method != "POST":
        return redirect("home")

    try:
        nombre = eliminar_archivo(archivo_id)
        messages.success(request, f"Archivo '{nombre}' eliminado correctamente.")
    except ValueError as e:
        messages.error(request, str(e))
    except Exception as e:
        messages.error(request, f"Error inesperado al eliminar: {str(e)}")

    return redirect("home")


@login_required(login_url='login')
def revertir_procesamiento_view(request, archivo_id):
    """
    Revierte el procesamiento de un archivo, eliminando sus Deudas y Cuotas.
    Solo acepta POST.
    """
    if request.method != "POST":
        return redirect("home")

    try:
        resumen = revertir_procesamiento(archivo_id)
        messages.success(
            request,
            f"Procesamiento de '{resumen['nombre_archivo']}' revertido correctamente. "
            f"Se eliminaron {resumen['deudas_eliminadas']} deudas y "
            f"{resumen['cuotas_eliminadas']} cuotas. "
            f"Podés subir el archivo corregido para el mismo período."
        )
    except ValueError as e:
        messages.error(request, str(e))
    except Exception as e:
        messages.error(request, f"Error inesperado al revertir: {str(e)}")

    return redirect("home")

@login_required(login_url='login')
def generar_liquidacion_view(request, archivo_id):
    """
    Genera la liquidación del 25% para un archivo procesado.
    Solo acepta POST.
    """
    if request.method != "POST":
        return redirect("home")

    try:
        liquidacion = generar_liquidacion(archivo_id, usuario=request.user)
        messages.success(
            request,
            f"Liquidación generada correctamente para {liquidacion.importacion.periodo.strftime('%B %Y')}. "
            f"Comisión: $ {liquidacion.comision:,.2f}"
        )
    except ValueError as e:
        messages.error(request, str(e))
    except Exception as e:
        messages.error(request, f"Error inesperado al generar la liquidación: {str(e)}")

    return redirect("home")


@login_required(login_url='login')
def descargar_liquidacion_view(request, liquidacion_id):
    """
    Descarga el PDF de una liquidación ya generada.
    """
    try:
        pdf_bytes, nombre = descargar_pdf_liquidacion(liquidacion_id)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{nombre}"'
        return response
    except ValueError as e:
        messages.error(request, str(e))
        return redirect("home")
    except Exception as e:
        messages.error(request, f"Error inesperado al descargar: {str(e)}")
        return redirect("home")