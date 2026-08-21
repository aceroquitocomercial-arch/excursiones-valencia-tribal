<?php
/**
 * Plugin Name: ValenciaTribe — Datos estructurados de excursiones
 * Description: Imprime el JSON-LD (FAQ/TouristTrip/Breadcrumb) guardado en cada producto.
 */
add_action('wp_head', function () {
    if (! function_exists('is_product') || ! is_product()) { return; }
    $id = get_queried_object_id();
    $product = wc_get_product($id);
    if (! $product) { return; }
    
    // Salvaguarda: si el precio está vacío o no es numérico, omitir para evitar errores fatales
    $price = $product->get_price();
    if (empty($price) || !is_numeric($price)) {
        return;
    }
    
    $ld = get_post_meta($id, '_tribal_jsonld', true);
    if (! empty($ld)) {
        echo "\n<script type=\"application/ld+json\">" . $ld . "</script>\n";
    }
}, 20);
