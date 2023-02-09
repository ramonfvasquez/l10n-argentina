###############################################################################
#
#    Copyright (c) 2018 Eynes/E-MIPS (www.eynes.com.ar)
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program. If not, see <http://www.gnu.org/licenses/>.
#
###############################################################################


from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


PCODES = {'nacional': '1', 'provincial': '2', 'municipal': '3'}


class PerceptionTaxLine(models.Model):
    _name = "perception.tax.line"
    _inherit = "perception.tax.line"

    manual = fields.Boolean(default=True)
    reg_code = fields.Integer('Reg. Code')
    tax_app_id = fields.Many2one('perception.tax.application',
                                 'Tax Application')
    concept_id = fields.Many2one('perception.concept', 'Concept')

    @api.onchange('perception_id')
    def onchange_perception_id(self):
        res = {}
        if self.perception_id:
            res['domain'] = {
                'concept_id': [('type', '=', self.perception_id.type)],
            }
        return res

    @api.model
    def get_readeable_name(self):
        """
        Returns an easy name used for exporters to print error messages
        """
        ptl_id = self.id
        ptl_name = self.name
        ptl_partner_name = self.partner_id.name or 'Undefined'
        name = u"RTL(#{0})[{1}-{2}]".format(ptl_id, ptl_name, ptl_partner_name)
        return name

    @api.v8
    def _compute(self, invoice, base, amount):
        """
        self: perception.tax.line
        invoice: account.invoice
        """
        # Nos fijamos la currency de la invoice
        currency = invoice.currency_id.with_context(date=invoice.date_invoice or fields.Date.context_today(invoice))
        company_currency = invoice.company_id.currency_id
        if invoice.type in ('out_invoice', 'in_invoice'):
            base_amount = currency.compute(base, company_currency, round=False)
            tax_amount = currency.compute(amount, company_currency, round=False)
        else:  # invoice is out_refund
            base_amount = currency.compute(base * -1, company_currency, round=False)
            tax_amount = currency.compute(amount * -1, company_currency, round=False)
        return (tax_amount, base_amount)


class AccountInvoice(models.Model):
    _name = "account.invoice"
    _inherit = "account.invoice"

    def _compute_amount(self):
        # self.compute_taxes()
        return super()._compute_amount()

    # Necesario para la aplicacion de las Percepciones de IIBB
    address_shipping_id = fields.Many2one(
        'res.partner', 'Shipping Address', readonly=True, required=False,
        states={
            'draft': [('readonly', False)],
        })

    @api.model
    def hook_add_taxes(self, inv, detalle):
        detalle = super().hook_add_taxes(inv, detalle)
        perc_array = []

        for perception in inv.perception_ids:
            code = PCODES[perception.perception_id.jurisdiccion]
            perc = {
                'Id': code,
                'BaseImp': perception.base,
                'Importe': perception.amount,
                'Alic': 0.0,
                'Desc': perception.name,
            }
            perc_array.append(perc)

        if detalle.get('Tributos'):
            detalle['Tributos']['Tributo'] += perc_array
        else:
            detalle['Tributos'] = {'Tributo': perc_array}

        return detalle

    @api.onchange('partner_id', 'company_id')
    def _onchange_partner_id(self):
        res = super(AccountInvoice, self)._onchange_partner_id()
        shipping_addr_id = False
        partner = self.partner_id
        if partner:
            addresses = partner.address_get(['delivery'])
            shipping_addr_id = addresses['delivery']
            self.address_shipping_id = shipping_addr_id

        return res

    @api.model
    def _prepare_refund(self, invoice, date_invoice=None,
                        date=None, description=None, journal_id=None):
        vals = super()._prepare_refund(
            invoice, date_invoice=date_invoice, date=date,
            description=description, journal_id=journal_id)
        perceptions = []
        for p in invoice.perception_ids:
            perceptions.append((0, 0, {
                'account_id': p.account_id.id,
                'ait_id': p.ait_id.id,
                'amount': p.amount,
                'base': p.base,
                'base_amount': p.base_amount,
                'company_id': p.company_id.id,
                'concept_id': p.concept_id.id,
                # 'date': datetime.date(2021, 10, 27),
                # 'invoice_id': (429, 'CI A00005-00000015'),
                'manual': p.manual,
                'name': p.name,
                'partner_id': p.partner_id.id,
                'perception_id': p.perception_id.id,
                'reg_code': p.reg_code,
                'state_id': p.state_id.id,
                'tax_amount': p.tax_amount,
                'tax_app_id': p.tax_app_id.id,
                'vat': p.vat,
            }))
        vals.update({
            'address_shipping_id': invoice.address_shipping_id.id,
            'perception_ids': perceptions,
        })
        return vals

    # Reescribimos esta funcion para lograr hacer el calculo
    # de Percepciones sobre IVA en dos fases. O sea, primero
    # creamos todas las account_invoice_tax menos las de
    # Percepciones y luego creamos las de Percepciones que
    # ya podemos calcularlas correctamente porque tenemos
    # los montos de IVA ya creados.
    @api.multi
    def get_taxes_values(self):
        ait_obj = self.env['account.invoice.tax']

        ctx = dict(self._context)

        for inv in self:
            # if new_id:
            #     self.env.cr.execute(
            #         """DELETE FROM account_invoice_tax
            #         WHERE invoice_id=%s AND manual is False""",
            #         (inv.id,))
            #     # Borramos las Percepciones calculadas anteriormente
            #     self.env.cr.execute(
            #         """DELETE FROM perception_tax_line
            #         WHERE invoice_id=%s AND manual is False""",
            #         (inv.id,))
            #     # Clear the cache after raw query's
            #     self.env.cache.invalidate()

            partner = inv.partner_id
            if partner.lang:
                ctx.update({'lang': partner.lang})

            # Esta clave(compute_perceptions=False) en el context es
            # importantisima. Lo hacemos para que no se calculen
            # las percepciones, ya que en este punto
            # desdoblamos el calculo de IVA y luego el de
            # Percepciones mas abajo. Si no agregasemos esta clave,
            # se calcularia todo, incluyendo las Percepciones
            # ctx.update({'compute_perceptions': False})
            # for taxe in self.get_taxes_values().values():
            #     ait_obj.create(taxe)

            # Calculo de Percepciones
            # if inv.type in ('out_invoice', 'out_refund'):

                # Calculamos cada Percepcion configurada en el Partner
                # perception_obj = self.env['perception.perception']
                # perc_lines = perception_obj.create_perceptions_from_partner(
                #         partner, date=inv.date_invoice, invoice=inv)
                # inv.perception_ids = perc_lines

            # this line gets commented to avoid duplicates
            # for taxe in ait_obj._compute_perception_invoice_taxes(inv).\
            #         values():
            #     ait_obj.new(taxe)

        # Update the stored value (fields.function),
        # so we write to trigger recompute
        # self.write({'invoice_line': []})
        return super().get_taxes_values()


class AccountInvoice_line(models.Model):
    _name = "account.invoice.line"
    _inherit = "account.invoice.line"

    @api.multi
    # TODO: Cambiarlo por el parseo del asiento contable
    def _compute_all_vat_taxes(self):
        tax_obj = self.env['account.tax']
        invoice = self.invoice_id
        currency = invoice.currency_id.with_context(
            date=invoice.date_invoice or fields.Date.context_today(invoice))
        company_currency = invoice.company_id.currency_id

        result = {}
        for line in self:
            result[line.id] = {
                'price_unit': 0.0,
                'amount_untaxed': 0.0,
                'amount_total': 0.0,
                'amount_no_taxed': 0.0,
                'amount_exempt': 0.0,
                'vat_taxes': {},
            }

            # Obtenemos los valores de la linea de factura
            line_taxes = {}
            taxes = line.invoice_line_tax_ids.compute_all(
                (line.price_unit * (1 - (line.discount or 0.0) / 100.0)),
                invoice.currency_id, line.quantity,
                line.product_id, invoice.partner_id)

            amount_tax = 0.0
            amount_base = 0.0
            amount_exempt = 0.0
            for tax in taxes['taxes']:
                res = tax_obj.browse(tax['id'])
                is_exempt = res.is_exempt
                tax_group = res.tax_group
                if not tax_group == 'vat':
                    continue

                val = {}
                val['invoice_line_id'] = line.id
                val['name'] = tax['name']
                val['amount'] = tax['amount']
                val['base'] = tax['base']
                val['base_amount'] = currency.compute(
                    val['base'],
                    company_currency, round=False)
                val['tax_amount'] = currency.compute(
                    val['amount'],
                    company_currency, round=False)
                val['account_id'] = tax['account_id'] or \
                    line.account_id.id

                amount_tax += tax['amount']
                if is_exempt:
                    amount_exempt += val['base']
                else:
                    amount_base += val['base']

                line_taxes[tax['id']] = val

            price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            price_unit = price
            amount_untaxed = line.price_subtotal
            amount_total = amount_tax + amount_untaxed
            amount_no_taxed = amount_untaxed - amount_base - amount_exempt
            amount_exempt = amount_exempt
            vat_taxes = line_taxes

            result[line.id]['price_unit'] = price_unit
            result[line.id]['amount_untaxed'] = amount_untaxed
            result[line.id]['amount_total'] = amount_total
            result[line.id]['amount_no_taxed'] = amount_no_taxed
            result[line.id]['amount_exempt'] = amount_exempt
            result[line.id]['amount_taxed'] = amount_base
            result[line.id]['vat_taxes'] = vat_taxes

        return result


class AccountInvoiceTax(models.Model):
    _name = "account.invoice.tax"
    _inherit = "account.invoice.tax"

    # Pasamos de largo esta funcion, porque si instalamos este modulo
    # hacemos calculos de Percepciones sobre IVA y por lo tanto, tenemos
    # que tener calculados primero los montos de IVA para luego poder
    # calcular las Percepciones. Asi que desdoblamos esta funcion para que
    # la creacion de las account.invoice.tax sea en dos fases. Una para los
    # impuestos generales y otra para las Percepciones

    @api.v8
    def hook_compute_invoice_taxes(self, invoice, tax_grouped):
        # Seteamos esta key(auto=True) en el context para avisar
        # a esta misma funcion en el modulo
        # l10n_ar_perceptions_basic que no tiene que calcular nada,
        # que directamente retorne.
        tax_grouped = super(
            AccountInvoiceTax,
            self.with_context(auto=True)).hook_compute_invoice_taxes(
                invoice, tax_grouped)

        if invoice.env.context.get('compute_perceptions', True):
            tt = self._compute_perception_invoice_taxes(invoice)
            tax_grouped.update(tt)

        return tax_grouped

    @api.v8
    def _compute_perception_invoice_taxes(self, invoice):
        currency = invoice.currency_id.with_context(
            date=invoice.date_invoice or fields.Date.context_today(invoice))
        company_currency = invoice.company_id.currency_id
        sign = -1
        if invoice.type in ('out_invoice', 'in_invoice'):
            sign = 1
        tax_grouped = self._compute_perception_taxes(
            invoice.perception_ids, currency,
            company_currency, sign=sign, invoice=invoice,
        )
        return tax_grouped

    def _prepare_perception_tax_line(self, line, currency, company_currency,
                                     sign=1, **kwargs):
        val = {}
        tax = line.perception_id.tax_id
        val['name'] = line.name
        val['amount'] = line.amount
        val['manual'] = False
        val['sequence'] = 10
        val['is_exempt'] = False
        val['base'] = line.base
        val['tax_id'] = tax.id

        # Computamos tax_amount y base_amount
        base_amount = currency.compute(
            line.base * sign,
            company_currency,
            round=False,
        )
        tax_amount = currency.compute(
            line.amount * sign,
            company_currency,
            round=False,
        )
        val['base'] = base_amount * sign
        val['amount'] = tax_amount * sign
        if sign > 0:
            val['account_id'] = tax.account_id.id
        else:
            val['account_id'] = tax.refund_account_id.id

        if 'invoice' in kwargs:
            val['invoice_id'] = kwargs['invoice'].id
        return val

    @api.v8
    def _compute_perception_taxes(self, perceptions, currency,
                                  company_currency, sign=1, **kwargs):
        tax_grouped = {}

        # Recorremos las percepciones y las computamos como account.invoice.tax
        for line in perceptions:
            line_vals = self._prepare_perception_tax_line(
                line, currency, company_currency,
                sign=sign, **kwargs,
            )
            key = (line_vals['account_id'])
            if key not in tax_grouped:
                tax_grouped[key] = line_vals
            else:
                tax_grouped[key]['amount'] += line_vals['amount']
                tax_grouped[key]['base'] += line_vals['base']
                # tax_grouped[key]['base_amount'] += val['base_amount']
                # tax_grouped[key]['tax_amount'] += val['tax_amount']

        for t in tax_grouped.values():
            t['base'] = currency.round(t['base'])
            t['amount'] = currency.round(t['amount'])
            # t['base'] = currency.round(t['base'])
            # t['tax_amount'] = currency.round(t['tax_amount'])

        return tax_grouped
