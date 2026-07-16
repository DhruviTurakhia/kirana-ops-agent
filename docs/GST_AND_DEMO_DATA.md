# GST and Demo Catalog Data

> **Important:** This repository is a software demonstration, not tax advice. The GST/HSN data is a curated reference snapshot and is not a binding classification or a substitute for the latest notification, the actual package label, the supplier tax invoice, or advice from a qualified Indian tax professional.

## What is implemented

The data layer separates tax facts from retail-demo facts:

- [`tax_rules.json`](../src/kirana_agent/data/tax_rules.json) contains 46 versioned tax rules, their applicability conditions, total GST rates, intra-state CGST/SGST splits, inter-state IGST rates, effective dates, verification dates, and official source references.
- [`products.json`](../src/kirana_agent/data/products.json) contains exactly 120 demo products. Every product references a valid `tax_rule_id`; the model does not invent a GST rate while billing.
- Product records use internal IDs such as `PS-001` and `NB-001`. The seed data does not invent or claim real GTINs/barcodes.
- Finalized bills should snapshot the selected HSN, GST rate, and tax-rule version on every line. A later rate update must never rewrite an old invoice.

The current snapshot is intended for transactions on or after **16 January 2025** and was verified against the linked official material on **15 July 2026**. That date is the applicability boundary of this dataset version, not necessarily the date on which every individual rate was first introduced.

## Packaging-aware classification

GST is not inferred from a simplistic `loose versus branded` flag. Several staple entries use the statutory **pre-packaged and labelled** condition. Notification No. 01/2025-Central Tax (Rate) describes this, in summary, as a commodity:

- intended for retail sale;
- containing not more than 25 kg or 25 litre;
- pre-packed as defined under the Legal Metrology Act; and
- in packaging, or with a securely affixed label, required to bear Legal Metrology declarations.

The catalog therefore stores both `tax_rule_id` and `packaging_tax_treatment`. Loose goods weighed or packed in the buyer's presence can point to a different rule from an otherwise similar retail pack. Product composition and specific schedule entries can still override packaging, so the billing tool always uses the stored rule rather than asking the language model to reason out a rate.

Representative catalog treatments are:

| Example | HSN | Demo treatment | Total GST | Intra-state split |
| --- | --- | --- | ---: | ---: |
| Loose wheat atta | 1101 | Other than pre-packaged and labelled | Nil | 0% + 0% |
| Aashirvaad Atta 5kg | 1101 | Pre-packaged and labelled | 5% | 2.5% CGST + 2.5% SGST |
| Loose rice / packaged rice | 1006 | Separate conditional rules | Nil / 5% | 0% + 0% / 2.5% + 2.5% |
| Loose dal / packaged dal | 0713 | Separate conditional rules | Nil / 5% | 0% + 0% / 2.5% + 2.5% |
| Fortune Sunflower Oil 1L | 1512 | Edible-oil classification | 5% | 2.5% + 2.5% |
| Amul Butter 100g | 0405 | Butter and milk-fat classification | 12% | 6% + 6% |
| Maggi Noodles 70g | 1902 | Noodles/pasta classification | 12% | 6% + 6% |
| Parle-G biscuits | 1905 | Biscuit classification | 18% | 9% + 9% |
| Surf Excel detergent | 3402 | Washing/cleaning preparation | 18% | 9% + 9% |

Two deliberately important edge cases are included:

1. **Tata Salt 1kg is Nil-rated in this snapshot.** HSN 2501 has a specific entry for salt, all types. A product does not become 5% merely because it is branded and packaged.
2. **Ordinary white sugar remains 5% even when sold loose.** HSN 1701 beet/cane sugar has a 5% entry. Do not apply the shortcut `loose staple = Nil`. Non-pre-packaged jaggery, khandsari sugar, and rab have a separate Nil-rate treatment.

These examples show why tax rules are explicit records and not prompt instructions.

## Intra-state and inter-state tax

For an ordinary intra-state supply, the total rate is divided equally between CGST and SGST:

| Total GST | CGST | SGST | Inter-state IGST |
| ---: | ---: | ---: | ---: |
| Nil | 0% | 0% | 0% |
| 5% | 2.5% | 2.5% | 5% |
| 12% | 6% | 6% | 12% |
| 18% | 9% | 9% | 18% |

The store state and place of supply determine the supply type. The agent must not choose CGST/SGST versus IGST from intuition. The billing tool should make this decision from persisted shop and transaction data, while retaining the statutory exceptions such as SEZ supplies.

## Tax-inclusive price math and rounding

MRP is inclusive of taxes, and every seed product has `price_tax_inclusive: true`. Cost, sell price, and MRP are integer paise per `sale_uom`; loose and fresh quantities are tracked in atomic grams, while packaged stock is tracked in pieces.

For a tax-inclusive line gross amount `G` and total GST rate `R`:

```text
taxable value = G × 100 / (100 + R)
total GST     = G × R   / (100 + R)
```

For an intra-state 12% item, each component is calculated directly from the inclusive gross:

```text
CGST = G × 6 / 112
SGST = G × 6 / 112
```

The calculation policy is:

1. Use decimal/fixed-point arithmetic, never binary floating point.
2. Preserve precision while calculating line values.
3. Aggregate taxable value and tax by slab and component before document rounding.
4. Quantize displayed rupee values to two decimal places using half-up rounding.
5. Assert that taxable value, CGST/SGST or IGST, and any explicit round-off reconcile to the invoice total.
6. Never round each line's tax independently to a whole rupee. Section 170 applies nearest-rupee rounding to statutory sums payable or refundable. If a whole-rupee adjustment is applied at an invoice/payment boundary, show one explicit `round_off` amount instead of hiding the difference.

This policy keeps small mixed-rate kirana bills reproducible and avoids the one-paise drift caused by early rounding.

## Demo catalog composition

The deterministic seed catalog contains:

| Category | Products |
| --- | ---: |
| Loose grains, flours, and pulses | 18 |
| Packaged staples | 16 |
| Oils, spices, and condiments | 16 |
| Dairy and refrigerated | 12 |
| Noodles, breakfast, and bakery | 10 |
| Biscuits, snacks, and confectionery | 14 |
| Beverages | 8 |
| Household cleaning | 10 |
| Personal care | 8 |
| Fresh produce | 8 |
| **Total** | **120** |

The required evaluator scenarios are available by familiar names and aliases: Aashirvaad Atta 5kg, Tata Salt 1kg, Amul Butter 100g, Fortune Sunflower Oil 1L, Maggi 70g, Parle-G, and Surf Excel.

Inventory fixtures are intentional:

- Maggi 70g has exactly **6 packets** so an attempt to sell 10 exercises the tool-layer oversell guard.
- Amul Butter 100g, Maggi 70g, and Fogg Body Spray are positive low-stock examples.
- Dettol Antiseptic Liquid 250ml is out of stock.

## Demo-data disclaimer

All product names, aliases, pack availability, costs, sell prices, MRPs, inventory quantities, and reorder levels are included only to demonstrate application behavior. Pricing and stock values are fictional and must not be represented as current prices in any Indian city or store. Brand names are familiar labels only; this project is not affiliated with or endorsed by those brands. Product logos and scraped packaging images are intentionally not part of the dataset.

The HSN mapping of a named SKU is an informed demo classification of its generic product type. A real product can differ because of its ingredients, preparation, packaging, size, labelling, or a later notification. For production, the supplier invoice and current official rules must be checked.

## Official sources

- [CBIC consolidated GST goods and services rates](https://cbic-gst.gov.in/gst-goods-services-rates.html)
- [GST Council central tax rate-notification index](https://gstcouncil.gov.in/cgst-rate-notification)
- [Notification No. 01/2025-Central Tax (Rate): pre-packaged and labelled definition](https://gstcouncil.gov.in/sites/default/files/2025-01/ctr01-2025.pdf)
- [IGST Act, including sections 7 and 8 on inter-state and intra-state supplies](https://cbic-gst.gov.in/hindi/IGST-bill-e.html)
- [CGST Rules, including Rule 35 for values inclusive of tax](https://gstcouncil.gov.in/sites/default/files/2024-04/01062021-cgst-rules-2017-part-a-rules.pdf)
- [CGST Act, including section 170 on rounding](https://cbic-gst.gov.in/hindi/CGST-bill-e.html)
- [CBIC tax-invoice requirements](https://cbic-gst.gov.in/gst-invoice-rules.html)
- [CBIC GST rate FAQs, including MRP as inclusive of GST](https://cbic-gst.gov.in/gst-rates-faq.html)

Official pages can themselves be amended or superseded. The source URLs and verification date in `tax_rules.json` make the snapshot auditable; they do not freeze the law.

## Verification and update checklist

Before using the data for a live store:

- [ ] Review the latest CBIC consolidated rate table and every subsequent relevant Central Tax (Rate) notification.
- [ ] Verify the shop state, customer/place-of-supply data, registration status, and any SEZ or other special treatment.
- [ ] Check each product's actual label, net quantity, pre-packaged status, composition, HSN, and supplier tax invoice.
- [ ] Confirm the HSN digit level required for the taxpayer and invoice type; the seed's example headings may not be sufficient for every production invoice.
- [ ] Add a new versioned tax rule with `effective_from` rather than mutating a rule used by historical bills.
- [ ] Update `verified_at` and retain the official source URL for every changed rule.
- [ ] Re-run integrity checks: unique internal SKUs, valid rule references, HSN membership, product/rule rate agreement, and `CGST + SGST = IGST = total GST` for ordinary rates.
- [ ] Re-run golden invoice tests for Nil, 5%, 12%, 18%, mixed-rate, inclusive-price, fractional loose quantity, one-paise boundary, and whole-rupee round-off cases.
- [ ] Confirm finalized invoices retain their original tax snapshot after an update.
- [ ] Obtain qualified tax review before removing any **DEMO / NOT TAX ADVICE** marking from generated documents.
