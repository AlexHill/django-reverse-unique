import django

from django.db.models import Q
from django.utils.functional import cached_property

from relativity.fields import Relationship, L


if django.VERSION >= (1, 9):
    def get_remote_field(field):
        return field.remote_field

    def get_remote_field_model(field):
        return field.remote_field.model
else:
    def get_remote_field(field):
        return getattr(field, 'rel', None)

    def get_remote_field_model(field):
        return field.rel.to


class ReverseUnique(Relationship):

    def __init__(self, *args, **kwargs):
        filters = kwargs.pop('filters')

        def predicate():
            unique_filters = filters() if callable(filters) else filters
            fk_filters = Q(**{
                from_field.attname: L(to_field.attname)
                for to_field, from_field
                in self.fk_related_fields
            })
            return fk_filters & unique_filters

        kwargs['predicate'] = predicate
        self.through = kwargs.pop('through', None)
        kwargs['null'] = True
        kwargs['related_name'] = '+'
        kwargs['multiple'] = False
        kwargs['reverse_multiple'] = False
        super(ReverseUnique, self).__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super(ReverseUnique, self).deconstruct()
        kwargs["filters"] = kwargs.pop('predicate')
        kwargs["through"] = self.through
        return name, path, args, kwargs

    @cached_property
    def fk_related_fields(self):
        if self.through is None:
            possible_models = [self.model] + [m for m in self.model.__mro__ if hasattr(m, '_meta')]
            possible_targets = [f for f in get_remote_field_model(self)._meta.concrete_fields
                                if get_remote_field(f) and get_remote_field_model(f) in possible_models]
            if len(possible_targets) != 1:
                raise Exception("Found %s target fields instead of one, the fields found were %s."
                                % (len(possible_targets), [f.name for f in possible_targets]))
            related_field = possible_targets[0]
        else:
            related_field = self.model._meta.get_field(self.through).field
        if get_remote_field_model(related_field)._meta.concrete_model != self.model._meta.concrete_model:
            # We have found a foreign key pointing to parent model.
            # This will only work if the fk is pointing to a value
            # that can be found from the child model, too. This is
            # the case only when we have parent pointer in child
            # pointing to same field as the found foreign key is
            # pointing to. Lets find this out. And, lets handle
            # only the single column case for now.
            if len(related_field.to_fields) > 1:
                raise ValueError(
                    "FIXME: No support for multi-column joins in parent join case."
                )
            to_fields = self._find_parent_link(related_field)
        else:
            to_fields = [f.name for f in related_field.foreign_related_fields]
        self.to_fields = [f.name for f in related_field.local_related_fields]
        self.from_fields = to_fields
        return super(Relationship, self).resolve_related_fields()

    def _find_parent_link(self, related_field):
        """
        Find a field containing the value of related_field in local concrete
        fields or raise an error if the value isn't available in local table.

        Technical reason for this is that parent model joining is done later
        than filter join production, and that means proucing a join against
        parent tables will not work.
        """
        # The hard part here is to verify that the value in fact can be found
        # from local field. Lets first build the ancestor link chain
        ancestor_links = []
        curr_model = self.model
        while True:
            found_link = curr_model._meta.get_ancestor_link(get_remote_field_model(related_field))
            if not found_link:
                # OK, we found to parent model. Lets check that the pointed to
                # field contains the correct value.
                last_link = ancestor_links[-1]
                if last_link.foreign_related_fields != related_field.foreign_related_fields:
                    curr_opts = curr_model._meta
                    rel_opts = get_remote_field_model(self)._meta
                    opts = self.model._meta
                    raise ValueError(
                        "The field(s) %s of model %s.%s which %s.%s.%s is "
                        "pointing to cannot be found from %s.%s. "
                        "Add ReverseUnique to parent instead." % (
                            ', '.join([f.name for f in related_field.foreign_related_fields]),
                            curr_opts.app_label, curr_opts.object_name,
                            rel_opts.app_label, rel_opts.object_name, related_field.name,
                            opts.app_label, opts.object_name
                        )
                    )
                break
            if ancestor_links:
                assert found_link.local_related_fields == ancestor_links[-1].foreign_related_fields
            ancestor_links.append(found_link)
            curr_model = get_remote_field_model(found_link)
        return [self.model._meta.get_ancestor_link(get_remote_field_model(related_field)).name]
