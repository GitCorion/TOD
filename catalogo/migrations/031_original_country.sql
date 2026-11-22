-- País de origen para distinguir español de España y español latinoamericano.
-- Películas: production_countries; series: origin_country. Se guarda como
-- códigos ISO separados por coma (por ejemplo "CO" o "ES,FR").
ALTER TABLE media_items ADD COLUMN original_country TEXT;
