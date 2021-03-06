import re

from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.core.urlresolvers import reverse
from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist
from django.template.response import TemplateResponse
from django.views.generic import DetailView

from oscar.view.generic import PostActionMixin
from oscar.core.loading import import_module

import_module('basket.models', ['Basket', 'Line', 'InvalidBasketLineError'], locals())
import_module('basket.forms', ['FormFactory'], locals())
import_module('basket.signals', ['basket_addition'], locals())
import_module('product.models', ['Item'], locals())
import_module('offer.models', ['Voucher'], locals())
    
        
class BasketView(DetailView, PostActionMixin):
    u"""Class-based view for the basket model."""
    template_name = 'oscar/basket/summary.html'
    context_object_name = 'basket'
    
    def __init__(self):
        self.response = HttpResponseRedirect(reverse('oscar-basket'))
    
    def get_object(self):
        u"""Return a basket model"""
        return self.request.basket
    
    def get_context_data(self, **kwargs):
        context = super(BasketView, self).get_context_data(**kwargs)
        if self.request.user.is_authenticated():
            try:
                context['saved_basket'] = Basket.saved.get(owner=self.request.user)
            except Basket.DoesNotExist:
                pass
        return context
            
    def do_flush(self, basket):
        u"""Flush basket content"""
        basket.flush()
        messages.info(self.request, "Your basket has been emptied")
        
    def do_add(self, basket):
        u"""Add an item to the basket"""
        item = get_object_or_404(Item.objects, pk=self.request.POST['product_id'])
        
        # Send signal so analytics can track this event.  Note that be emitting
        # the signal here, we do not track quantity changes to a product - only
        # the initial "add".
        basket_addition.send(sender=self, product=item, user=self.request.user)
        
        factory = FormFactory()
        form = factory.create(item, self.request.POST)
        if not form.is_valid():
            print form.errors
            self.response = HttpResponseRedirect(item.get_absolute_url())
            messages.error(self.request, "Unable to add your item to the basket - submission not valid")
        else:
            # Extract product options from POST
            options = []
            for option in item.options:
                if option.code in form.cleaned_data:
                    options.append({'option': option, 'value': form.cleaned_data[option.code]})
            basket.add_product(item, form.cleaned_data['quantity'], options)
            
            messages.info(self.request, "'%s' (quantity %d) has been added to your basket" %
                          (item.get_title(), form.cleaned_data['quantity']))
    
    def do_add_voucher(self, basket):
        code = self.request.POST['voucher_code']
        # First check if the voucher is already in the basket
        try:
            voucher = basket.vouchers.get(code=code)
            messages.error(self.request, "You have already added the '%s' voucher to your basket" % voucher.code)
            return
        except ObjectDoesNotExist:    
            pass
        
        try:
            voucher = Voucher._default_manager.get(code=code)
            if not voucher.is_active():
                messages.error(self.request, "The '%s' voucher has expired" % voucher.code)
                return
            is_available, message = voucher.is_available_to_user(self.request.user)
            if not is_available:
                messages.error(self.request, message)
                return
            
            basket.vouchers.add(voucher)
            basket.save()
            messages.info(self.request, "Voucher '%s' added to basket" % voucher.code)
        except ObjectDoesNotExist:
            messages.error(self.request, "No voucher found with code '%s'" % code)
            
    def do_remove_voucher(self, basket):
        code = self.request.POST['voucher_code']
        try:
            voucher = basket.vouchers.get(code=code)
            basket.vouchers.remove(voucher)
            basket.save()
            messages.info(self.request, "Voucher '%s' removed from basket" % voucher.code)
        except ObjectDoesNotExist:
            messages.error(self.request, "No voucher found with code '%s'" % code)
            
    def do_bulk_load(self, basket):
        num_additions = 0
        num_not_found = 0
        for sku in re.findall(r"[\d -]{5,}", self.request.POST['source_text']):
            try:
                item = Item.objects.get(upc=sku)
                basket.add_product(item)
                num_additions += 1
            except Item.DoesNotExist:
                num_not_found += 1
        messages.info(self.request, "Added %d items to your basket (%d missing)" % (num_additions, num_not_found))
 

class LineView(DetailView, PostActionMixin):
    
    def __init__(self, **kwargs):
        super(LineView, self).__init__(**kwargs)
        self.response = HttpResponseRedirect(reverse('oscar-basket'))
    
    def get_object(self):
        """
        Returns the basket line in question
        """
        return self.request.basket.lines.get(line_reference=self.kwargs['line_reference'])
        
    def post(self, request, *args, **kwargs):
        u"""Handle POST requests against the basket line"""
        try:
            return super(LineView, self).post(request, *args, **kwargs)
        except Line.DoesNotExist:
            messages.error(self.request, "Unable to find a line with reference %s in your basket" % self.kwargs['line_reference'])
        except InvalidBasketLineError, e:
            messages.error(self.request, str(e))
        return self.response
            
    def get_quantity(self):
        u"""Get item quantity"""
        if 'quantity' in self.request.POST:
            return int(self.request.POST['quantity'])
        return 0        
            
    def do_increment_quantity(self, line):
        u"""Increment item quantity"""
        q = self.get_quantity()
        line.quantity += q
        line.save()    
        msg = "The quantity of '%s' has been increased by %d" % (line.product, q)
        messages.info(self.request, msg)
        
    def do_decrement_quantity(self, line):
        u"""Decrement item quantity"""
        q = self.get_quantity()
        line.quantity -= q
        line.save()    
        msg = "The quantity of '%s' has been decreased by %d" % (line.product, q)
        messages.info(self.request, msg)
        
    def do_set_quantity(self, line):
        u"""Set an item quantity"""
        q = self.get_quantity()
        line.quantity = q
        line.save()    
        msg = "The quantity of '%s' has been set to %d" % (line.product, q)
        messages.info(self.request, msg)
        
    def do_delete(self, line):
        u"""Delete a basket item"""
        line.delete()
        msg = "'%s' has been removed from your basket" % line.product
        messages.info(self.request, msg)
        
    def do_save_for_later(self, line):
        u"""Save basket for later use"""
        if not self.request.user.is_authenticated():
            messages.error(self.request, "Only signed in users can save basket lines")
            return
        saved_basket, _ = Basket.saved.get_or_create(owner=self.request.user)
        saved_basket.merge_line(line)
        msg = "'%s' has been saved for later" % line.product
        messages.info(self.request, msg)
        
       
class SavedLineView(DetailView, PostActionMixin):
    
    def __init__(self):
        self.response = HttpResponseRedirect(reverse('oscar-basket'))
    
    def get_object(self):
        basket = Basket.saved.get(owner=self.request.user)
        return basket.lines.get(line_reference=self.kwargs['line_reference'])
            
    def do_move_to_basket(self, line):
        u"""Merge line items in to current basket"""
        real_basket = self.request.basket
        real_basket.merge_line(line)
        msg = "'%s' has been moved back to your basket" % line.product
        messages.info(self.request, msg)
        
    def do_delete(self, line):
        u"""Delete line item"""
        line.delete()
        msg = "'%s' has been removed" % line.product
        messages.warn(self.request, msg)
        
    def get_quantity(self):
        u"""Get line item quantity"""
        if 'quantity' in self.request.POST:
            return int(self.request.POST['quantity'])
        return 0
