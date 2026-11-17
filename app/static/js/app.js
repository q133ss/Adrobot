document.addEventListener("alpine:init", () => {
  Alpine.data("offerPicker", (config = {}) => ({
    query: "",
    selected: null,
    results: [],
    open: false,
    loading: false,
    error: "",

    init() {
      if (config.initialId) {
        this.selected = { id: config.initialId, name: config.initialName || `#${config.initialId}` };
        this.query = this.selected.name;
      }
    },

    focus() {
      this.open = true;
      if (!this.results.length) this.search();
    },

    async search() {
      if (this.selected && this.query === this.selected.name) return;
      this.loading = true;
      this.error = "";
      try {
        const response = await fetch(`/api/offers?q=${encodeURIComponent(this.query)}`, {
          headers: { Accept: "application/json" },
        });
        const data = await response.json();
        this.results = data.offers || [];
        if (data.error) this.error = data.error;
      } catch (exc) {
        this.error = "Не удалось загрузить офферы";
      } finally {
        this.loading = false;
      }
    },

    choose(offer) {
      this.selected = offer;
      this.query = offer.name;
      this.open = false;
    },

    clear() {
      this.selected = null;
      this.query = "";
      this.results = [];
      this.open = true;
      this.search();
    },
  }));
});
