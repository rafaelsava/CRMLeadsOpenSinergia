import unittest
from email.message import EmailMessage

from mailparse import parse_form_email

OWN = {"leads@opensinergia.com"}


def make_email(text=None, html=None, sender="WordPress <wordpress@opensinergia.com>", reply_to=None,
               subject="Nuevo mensaje del formulario", date="Fri, 09 Oct 2026 14:02:00 +0000"):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "leads@opensinergia.com"
    msg["Subject"] = subject
    msg["Message-ID"] = "<abc123@opensinergia.com>"
    if date:
        msg["Date"] = date
    if reply_to:
        msg["Reply-To"] = reply_to
    if text is not None:
        msg.set_content(text)
        if html is not None:
            msg.add_alternative(html, subtype="html")
    else:
        msg.set_content(html, subtype="html")
    return msg.as_bytes()


class ParseFormEmailTest(unittest.TestCase):
    def test_contact_form_7_spanish_template(self):
        lead = parse_form_email(make_email(
            "De: Ana Pérez <ana.perez@gmail.com>\n"
            "Asunto: Cotización página web\n\n"
            "Cuerpo del mensaje:\n"
            "Hola, necesito una página web para mi empresa.\n"
            "Presupuesto aproximado: 3 millones\n\n"
            "-- \n"
            "Este mensaje se envió desde un formulario de contacto en OpenSinergia (https://opensinergia.com)\n"), OWN)
        self.assertEqual(lead["name"], "Ana Pérez")
        self.assertEqual(lead["email"], "ana.perez@gmail.com")
        self.assertEqual(lead["interest"], "Cotización página web")
        self.assertIn("necesito una página web", lead["message"])
        self.assertIn("Presupuesto aproximado: 3 millones", lead["message"])
        self.assertNotIn("formulario de contacto en", lead["message"])
        self.assertFalse(lead["needs_review"])
        self.assertEqual(lead["message_id"], "<abc123@opensinergia.com>")
        self.assertEqual(lead["received_at"], "2026-10-09T14:02:00+00:00")

    def test_elementor_html_with_metadata_footer(self):
        lead = parse_form_email(make_email(html=(
            "<html><body><p>Nombre: Carlos Ruiz<br>Correo: carlos@empresa.co<br>Teléfono: +57 310 555 1234<br>"
            "Empresa: Empresa SAS<br>Servicio: Marketing digital<br>Mensaje: Quiero pauta en Google<br>"
            "y en Meta</p><p>---<br>Date: October 9, 2026<br>Page URL: https://opensinergia.com/contacto<br>"
            "Powered by: Elementor</p></body></html>")), OWN)
        self.assertEqual(lead["name"], "Carlos Ruiz")
        self.assertEqual(lead["email"], "carlos@empresa.co")
        self.assertEqual(lead["phone"], "+57 310 555 1234")
        self.assertEqual(lead["company"], "Empresa SAS")
        self.assertEqual(lead["interest"], "Marketing digital")
        self.assertEqual(lead["message"], "Quiero pauta en Google\ny en Meta")

    def test_wpforms_table_with_label_on_its_own_row(self):
        lead = parse_form_email(make_email(html=(
            "<table><tr><td><strong>Nombre</strong></td></tr><tr><td>María López</td></tr>"
            "<tr><td><strong>Correo electrónico</strong></td></tr><tr><td>maria@lopez.com</td></tr>"
            "<tr><td><strong>Comentario o mensaje</strong></td></tr><tr><td>Necesito soporte<br>urgente</td></tr>"
            "</table>")), OWN)
        self.assertEqual(lead["name"], "María López")
        self.assertEqual(lead["email"], "maria@lopez.com")
        self.assertEqual(lead["message"], "Necesito soporte\nurgente")

    def test_two_column_html_table(self):
        lead = parse_form_email(make_email(html=(
            "<table><tr><th>Nombre completo</th><td>Pedro Gil</td></tr>"
            "<tr><th>WhatsApp</th><td>300 123 4567</td></tr></table>")), OWN)
        self.assertEqual(lead["name"], "Pedro Gil")
        self.assertEqual(lead["phone"], "300 123 4567")

    def test_colon_lines_inside_message_stay_in_message(self):
        lead = parse_form_email(make_email(
            "Nombre: Luis\nCorreo: luis@x.co\nMensaje: Necesito lo siguiente:\n- tienda en línea\n"
            "Detalle importante: con pasarela de pagos\nGracias\n"), OWN)
        self.assertEqual(lead["message"],
                         "Necesito lo siguiente:\n- tienda en línea\nDetalle importante: con pasarela de pagos\nGracias")

    def test_unknown_fields_are_kept_at_the_end_of_the_message(self):
        lead = parse_form_email(make_email("Nombre: Sara\nCiudad: Bogotá\nCorreo: sara@x.co\nMensaje: Hola\n"), OWN)
        self.assertEqual(lead["message"], "Hola\n\nCiudad: Bogotá")

    def test_reply_to_is_used_when_the_body_has_no_email(self):
        lead = parse_form_email(make_email("Nombre: Pedro\nMensaje: Hola", reply_to="Pedro Gómez <pedro@x.co>"), OWN)
        self.assertEqual(lead["email"], "pedro@x.co")
        self.assertEqual(lead["name"], "Pedro")

    def test_name_falls_back_to_sender_display_name_but_not_generic_ones(self):
        lead = parse_form_email(make_email("Correo: ana@x.co\nMensaje: Hola", sender="Ana Ruiz <wordpress@site.co>"), OWN)
        self.assertEqual(lead["name"], "Ana Ruiz")
        lead = parse_form_email(make_email("Correo: ana@x.co\nMensaje: Hola"), OWN)
        self.assertEqual(lead["name"], "")

    def test_email_without_contact_data_needs_review_and_keeps_the_text(self):
        lead = parse_form_email(make_email("hola, quiero información"), OWN)
        self.assertTrue(lead["needs_review"])
        self.assertEqual(lead["message"], "hola, quiero información")
        self.assertEqual(lead["email"], "")

    def test_own_addresses_are_never_taken_as_the_lead_email(self):
        lead = parse_form_email(make_email("Mensaje: escríbannos a leads@opensinergia.com",
                                           reply_to="leads@opensinergia.com"), OWN)
        self.assertEqual(lead["email"], "")

    def test_missing_date_and_message_id(self):
        raw = make_email("Nombre: Ana\nCorreo: ana@x.co", date=None).replace(
            b"Message-ID: <abc123@opensinergia.com>\n", b"")
        lead = parse_form_email(raw, OWN)
        self.assertIsNone(lead["received_at"])
        self.assertTrue(lead["message_id"].startswith("sha256:"))

class OpenSinergiaFormTest(unittest.TestCase):
    """Campos del formulario de opensinergia (según Fermin): mismo formato, distintos diseños de correo."""

    EXPECTED = {"name": "Laura Gómez", "email": "laura@estudiolg.co", "phone": "", "company": "Estudio LG",
                "interest": "Desarrollo web", "message": "Necesito una página con portafolio.\nY un formulario."}

    def check(self, lead):
        for key, value in self.EXPECTED.items():
            self.assertEqual(lead[key], value, key)
        self.assertFalse(lead["needs_review"])

    def test_label_and_value_on_the_same_line(self):
        self.check(parse_form_email(make_email(
            "Nombre completo: Laura Gómez\nCorreo Electronico: laura@estudiolg.co\nTelefono (Opcional): \n"
            "Empresa (Opcional): Estudio LG\nServicio de interes: Desarrollo web\n"
            "Mensaje (¿Como podemos ayudarte?): Necesito una página con portafolio.\nY un formulario.\n"), OWN))

    def test_label_on_its_own_line(self):
        self.check(parse_form_email(make_email(
            "Nombre completo\nLaura Gómez\n\nCorreo Electronico\nlaura@estudiolg.co\n\nTelefono (Opcional)\n(Empty)\n\n"
            "Empresa (Opcional)\nEstudio LG\n\nServicio de interes\nDesarrollo web\n\n"
            "Mensaje (¿Como podemos ayudarte?)\nNecesito una página con portafolio.\nY un formulario.\n"), OWN))

    def test_html_table(self):
        rows = [("Nombre completo", "Laura Gómez"), ("Correo Electronico", "laura@estudiolg.co"),
                ("Telefono (Opcional)", ""), ("Empresa (Opcional)", "Estudio LG"),
                ("Servicio de interes", "Desarrollo web"),
                ("Mensaje (¿Como podemos ayudarte?)", "Necesito una página con portafolio.<br>Y un formulario.")]
        html = "<table>" + "".join(f"<tr><td><b>{k}</b></td><td>{v}</td></tr>" for k, v in rows) + "</table>"
        self.check(parse_form_email(make_email(html=html), OWN))


if __name__ == "__main__":
    unittest.main()
